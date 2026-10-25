import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image, ImageChops
from assemble_nicole_animation import _full_frame_paths, _inputs, _durations, _save, assemble
from validate_nicole_release import audit
import json


class PackagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_api_sample_suffix_is_not_frame_number(self):
        for number in (10, 2, 1):
            (self.root / f'nicole-idle-frame-{number:03}-01.png').touch()
        self.assertEqual([p.name for p in _full_frame_paths(self.root)],
                         [f'nicole-idle-frame-{n:03}-01.png' for n in (1, 2, 10)])

    def test_duplicate_outputs_fail(self):
        for sample in (1, 2):
            (self.root / f'nicole-idle-frame-001-{sample:02}.png').touch()
        with self.assertRaisesRegex(ValueError, '重复帧'):_full_frame_paths(self.root)

    def test_missing_sequence_rejected_before_touching_output(self):
        folder = self.root / 'idle';folder.mkdir()
        for n in range(1, 104):
            (folder / f'nicole-idle-frame-{n:03}-01.png').touch()
        output = self.root / 'output'
        with self.assertRaisesRegex(ValueError, '缺失.*104'):
            assemble(self.root, output, actions=['idle'])
        self.assertFalse(output.exists())

    def test_complete_sequence_uses_all_104_frames(self):
        folder = self.root / 'idle';folder.mkdir()
        for n in reversed(range(1, 105)):
            (folder / f'nicole-idle-frame-{n:03}-01.png').touch()
        paths, full, count = _inputs('idle', self.root)
        self.assertTrue(full);self.assertEqual(count, 104)
        self.assertEqual(paths[-1].name, 'nicole-idle-frame-104-01.png')

    def test_durations_follow_uniform_24fps_clock(self):
        for count, total in ((104,4333),(163,6792),(56,2333)):
            delays = _durations(total, count)
            self.assertEqual(sum(delays), total)
            self.assertLessEqual(set(delays), {41,42})
            for end in range(1,count+1):
                self.assertLessEqual(abs(sum(delays[:end])-end*total/count),0.5)

    def test_lossless_encoding_preserves_visible_pixels_and_time(self):
        frames = [Image.new('RGBA',(32,32),(i*30,100,200,128)) for i in range(4)]
        path = self.root/'test.webp'
        info = _save(frames,[42,41,42,42],path,method=1)
        self.assertEqual((info['encodedFrames'],info['durationMs']),(4,167))
        with Image.open(path) as decoded:
            for i, frame in enumerate(frames):
                decoded.seek(i)
                self.assertIsNone(ImageChops.difference(decoded.convert('RGBA'),frame).getbbox())

    def test_encoding_failure_preserves_previous_output(self):
        path = self.root/'test.webp';path.write_bytes(b'previous good file')
        frame = Image.new('RGBA',(32,32),'red')
        with patch.object(Image.Image,'save',side_effect=OSError('full disk')):
            with self.assertRaises(OSError):_save([frame],[42],path)
        self.assertEqual(path.read_bytes(),b'previous good file')
        self.assertEqual(list(self.root.glob('*.tmp.webp')),[])

    def small_release(self):
        frames=[]
        for i in range(3):
            image=Image.new('RGBA',(720,720),(0,0,0,0))
            image.paste((50*i,100,150,255),(10,10,80,80))
            image.putpixel((10,10),(50*i,100,150,128));frames.append(image)
        _save(frames,[42,41,42],self.root/'idle.webp')
        (self.root/'素材清单.json').write_text(json.dumps(dict(actions=[dict(id='idle',file='idle.webp',interpolation='direct-api-frames')])))

    def test_release_mechanical_success_is_not_visual_approval(self):
        self.small_release()
        with patch('validate_nicole_release.ACTION_DURATIONS',{'idle':125}):
            report=audit(self.root)
        self.assertEqual(report['mechanicalStatus'],'passed')
        self.assertFalse(report['readyToPublish'])

    def test_release_review_must_match_file_hash(self):
        self.small_release()
        review=self.root/'review.json'
        review.write_text(json.dumps(dict(status='passed',playbackChecked=True,files={'idle.webp':'wrong'})))
        with patch('validate_nicole_release.ACTION_DURATIONS',{'idle':125}):
            report=audit(self.root,review)
            self.assertFalse(report['readyToPublish'])
            review.write_text(json.dumps(dict(status='passed',playbackChecked=True,
                files={name:info['sha256'] for name,info in report['files'].items()})))
            self.assertTrue(audit(self.root,review)['readyToPublish'])

    def test_incomplete_manifest_cannot_be_published(self):
        (self.root/'素材清单.json').write_text('{"actions": []}')
        self.assertEqual(audit(self.root)['mechanicalStatus'],'failed')

    def test_old_packer_frame_order_is_rejected(self):
        self.small_release()
        path=self.root/'素材清单.json';manifest=json.loads(path.read_text())
        manifest['actions'][0]['sourceKeyframes']=[f'nicole-idle-frame-{n:03}-01.png' for n in (2,1,3)]
        path.write_text(json.dumps(manifest))
        with patch('validate_nicole_release.ACTION_DURATIONS',{'idle':125}):
            report=audit(self.root)
        self.assertEqual(report['mechanicalStatus'],'failed')
        self.assertTrue(any('帧序' in error for error in report['errors']))


if __name__ == '__main__':unittest.main()
