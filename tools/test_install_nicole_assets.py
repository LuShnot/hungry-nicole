import hashlib,json,tempfile,unittest,zipfile
from pathlib import Path
from install_nicole_assets import extract_checked,sha256

class InstallTests(unittest.TestCase):
    def make(self,root,entries):
        archive=root/'pack.zip'
        with zipfile.ZipFile(archive,'w') as z:
            for name,data in entries:z.writestr(name,data)
        return archive,dict(sha256=sha256(archive),files={n:hashlib.sha256(d).hexdigest() for n,d in entries})
    def test_valid_package_and_each_file_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);name='妮可桌宠素材/素材清单.json'
            archive,spec=self.make(root,[(name,b'{}')]);extract_checked(archive,root/'ok',spec)
            self.assertEqual((root/'ok'/name).read_bytes(),b'{}')
            spec['files'][name]='0'*64
            with self.assertRaises(ValueError):extract_checked(archive,root/'bad',spec)
    def test_rejects_tamper_missing_duplicate_and_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for entries in [[('妮可桌宠素材/../../outside',b'x')],[('elsewhere/x',b'x')],[('妮可桌宠素材/x',b'x'),('妮可桌宠素材/x',b'x')]]:
                archive,spec=self.make(root,entries)
                with self.assertRaises(ValueError):extract_checked(archive,root/'out',spec)
            archive,spec=self.make(root,[('妮可桌宠素材/x',b'x')]);spec['sha256']='0'*64
            with self.assertRaises(ValueError):extract_checked(archive,root/'out',spec)
            spec['sha256']=sha256(archive);spec['files']={}
            with self.assertRaises(ValueError):extract_checked(archive,root/'out',spec)

if __name__=='__main__':unittest.main()
