"""Offline checks for paid-task resumption and continuous media conversion."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch
import wave

import imageio_ffmpeg
from PIL import Image, ImageDraw

from nicole_video_plan import load_plan, select_jobs, atomic_json, digest
from prepare_nicole_video_bases import prepare
from nicole_seedance import request_plan, encode_payload, process_job, action_lock
from extract_nicole_video import extract
from check_nicole_video_bases import check as check_video_bases


class SeedanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        base = Image.new('RGBA', (720, 720))
        base.paste((240, 150, 200, 255), (250, 60, 470, 690))
        self.source = self.root / 'identity.png'
        base.save(self.source)
        self.bases = self.root / 'bases'
        self.plan = load_plan()
        prepare(self.source, self.bases)
        self.review = request_plan(self.plan, self.plan['actions'][0], self.bases)
        self.folder = self.root / 'idle'

    def quiet(self, **kw):
        with contextlib.redirect_stdout(io.StringIO()):
            return process_job(self.review, self.folder, **kw)

    def test_actions_and_consistent_bases(self):
        jobs = select_jobs(self.plan)
        self.assertEqual(len(jobs), 25)
        self.assertEqual(len(select_jobs(self.plan, include_care=True)), 27)
        self.assertEqual(digest(self.bases / '01/first.png'), digest(self.bases / 'idle/first.png'))
        self.assertEqual(digest(self.bases / 'landing/last.png'), digest(self.bases / 'idle/first.png'))
        self.assertNotEqual(digest(self.bases / 'landing/first.png'), digest(self.bases / 'landing/last.png'))
        self.assertFalse(any(j['generate_audio'] for j in jobs if j['group'] in ('idle', 'work')))

    def test_payload_modes_and_no_embedded_images_in_plan(self):
        payload = encode_payload(self.review)
        self.assertEqual([v['role'] for v in payload['content'][1:]], ['first_frame', 'last_frame'])
        self.assertFalse(payload['watermark'])
        self.assertTrue(payload['content'][1]['image_url']['url'].startswith('data:image/png;base64,'))
        self.assertNotIn('base64', json.dumps(self.review))

    def test_resume_never_posts_twice(self):
        client = Mock()
        client.request.return_value = {'id': 'cgt-test'}
        self.quiet(client=client, execute=True, submit_only=True)
        self.quiet(client=client, execute=True, submit_only=True)
        self.assertEqual(client.request.call_count, 1)
        self.assertEqual(json.loads((self.folder / 'task.json').read_text())['task_id'], 'cgt-test')

    def test_lost_response_cannot_automatically_recharge(self):
        client = Mock()
        client.request.side_effect = TimeoutError('secret=do-not-log')
        with self.assertRaisesRegex(ValueError, '提交未确认'):
            self.quiet(client=client, execute=True)
        with self.assertRaisesRegex(ValueError, '上次提交结果不明'):
            self.quiet(client=client, execute=True)
        self.assertEqual(client.request.call_count, 1)
        self.assertNotIn('secret', (self.folder / 'task.json').read_text())

    def test_poll_only_does_not_create(self):
        client = Mock()
        with self.assertRaisesRegex(ValueError, '没有任务 ID'):
            self.quiet(client=client)
        client.request.assert_not_called()

    def test_prompt_change_rejects_existing_batch(self):
        client = Mock()
        client.request.return_value = {'id': 'cgt-test'}
        self.quiet(client=client, execute=True, submit_only=True)
        self.review['signature'] = 'different'
        with self.assertRaisesRegex(ValueError, '不同 Prompt'):
            self.quiet(client=client, execute=True)
        self.assertEqual(client.request.call_count, 1)

    def test_terminal_failure_not_resubmitted(self):
        client = Mock()
        client.request.side_effect = [{'id': 'cgt-test'}, {'status': 'failed', 'error': {'code': 'BadInput', 'message': 'secret'}}]
        with self.assertRaisesRegex(ValueError, '任务 failed'):
            self.quiet(client=client, execute=True)
        with self.assertRaisesRegex(ValueError, '失败任务'):
            self.quiet(client=client, execute=True)
        self.assertEqual(client.request.call_count, 2)
        self.assertNotIn('secret', (self.folder / 'task.json').read_text())

    def test_success_receipt_drops_signed_url(self):
        client = Mock()
        client.request.side_effect = [{'id': 'cgt-test'}, {'status': 'succeeded',
            'content': {'video_url': 'https://example.org/source.mp4?Signature=secret'}, 'usage': {'total_tokens': 9}}]
        with patch('nicole_seedance.download', side_effect=lambda url, path: path.write_bytes(b'video')):
            self.quiet(client=client, execute=True)
        text = (self.folder / 'task.json').read_text()
        self.assertNotIn('Signature', text)
        self.assertNotIn('secret', text)
        self.quiet(client=client, execute=True)
        self.assertEqual(client.request.call_count, 2)

    def test_concurrent_lock_prevents_duplicate_creation(self):
        client = Mock()
        with action_lock(self.folder):
            with self.assertRaisesRegex(ValueError, '另一进程'):
                self.quiet(client=client, execute=True)
        client.request.assert_not_called()

    def test_reject_opaque_identity(self):
        Image.new('RGB', (720, 720), 'white').save(self.source)
        with self.assertRaisesRegex(ValueError, '透明 RGBA'):
            prepare(self.source, self.bases)

    def test_video_base_alignment_report(self):
        report = check_video_bases(self.bases)
        self.assertTrue(report['passed'])
        self.assertEqual(report['counts']['actions'], 27)
        self.assertEqual(report['counts']['samePixelFirstLast'], 26)
        self.assertEqual(report['counts']['differentPixelFirstLast'], 1)
        self.assertEqual(report['standingBBox'][0], report['airborneBBox'][0])
        self.assertEqual(report['standingBBox'][2], report['airborneBBox'][2])
        self.assertEqual(report['standingBBox'][3] - report['airborneBBox'][3], 68)


class MediaTests(unittest.TestCase):
    def test_real_video_time_alpha_audio_and_repeat(self):
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            video = root / 'fixture.mp4'
            # Continuous motion over chroma, plus a real audio stream.
            raw = []
            for index in range(24):
                im = Image.new('RGB', (144, 144), '#00ff00')
                ImageDraw.Draw(im).rectangle((40 + index, 20, 84 + index, 120), fill='pink')
                raw.append(im.tobytes())
            command = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-f', 'rawvideo',
                       '-pix_fmt', 'rgb24', '-s', '144x144', '-r', '24', '-i', 'pipe:0',
                       '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=1',
                       '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', str(video)]
            subprocess.run(command, input=b''.join(raw), check=True, capture_output=True, timeout=60)
            result = extract(video, root / 'out', start=.25, duration=.5)
            self.assertEqual(result['frames'], 12)
            self.assertEqual(result['durationMs'], 500)
            self.assertEqual(result['visualStatus'], 'pending')
            self.assertFalse(result['readyToPublish'])
            with Image.open(root / 'out' / result['animationFile']) as im:
                im.load()
                self.assertEqual(im.size, (720, 720))
                self.assertEqual(im.convert('RGBA').getpixel((5, 5))[3], 0)
                self.assertGreater(im.convert('RGBA').getpixel((360, 360))[3], 240)
                left = im.convert('RGBA').getchannel('A').getbbox()[0]
                im.seek(im.n_frames - 1)
                self.assertGreater(im.convert('RGBA').getchannel('A').getbbox()[0], left)
            with wave.open(str(root / 'out' / result['audioFile'])) as wav:
                self.assertEqual(wav.getframerate(), 48000)
                self.assertAlmostEqual(wav.getnframes() / 48000, .5, places=3)
            self.assertEqual(result, extract(video, root / 'out', start=.25, duration=.5))
            with self.assertRaisesRegex(ValueError, '不同参数'):
                extract(video, root / 'out', start=0., duration=.5)


if __name__ == '__main__':
    unittest.main()
