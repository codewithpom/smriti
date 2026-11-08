import contextlib
import io
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

import name_sheet


def write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def fixture(root):
    data = root / "data"
    data.mkdir()
    (root / "img/web").mkdir(parents=True)
    Image.new("RGB", (20, 16), "red").save(root / "img/web/p1.webp")
    faces = [{"i": i, "bbox": [0, 0, .3, .4], "status": "unknown", "cluster": i // 2 + 1} for i in range(4)]
    write(data / "faces.json", {"p1": faces})
    write(data / "faces_raw.json", [{"photo": "p1", "faces": [{"bbox": f["bbox"], "e": i, "px": 10 + i, "det": .9} for i, f in enumerate(faces)]}])
    write(data / "photos.json", [{"id": "p1", "web": "img/web/p1.webp"}])
    write(data / "people_meta.json", {"S0001": {"id": "S0001", "name": "Test One", "kind": "student", "reg_no": "TEST001"}})
    write(data / "ref_ids.json", [])
    write(data / "corrections.json", {"faces": {"p1:0": "S0001"}})
    write(data / "questions.json", [{"id": f"c{c}", "type": "cluster", "cluster": c, "faces": [f"p1:{i}" for i in range(4) if i // 2 + 1 == c], "cands": ["S0001"]} for c in (1, 2)])
    np.save(data / "face_emb.npy", np.eye(4))
    return data


class HelpersTest(unittest.TestCase):
    def test_crop_edges(self):
        for bbox in ([0, 0, .2, .2], [.9, .9, .1, .1], [0, 0, 1, 1]):
            l, t, r, b = name_sheet.crop_box(bbox, 100, 80)
            self.assertTrue(0 <= l < r <= 100 and 0 <= t < b <= 80)
            self.assertEqual(r - l, b - t)

    def test_pick(self):
        members = [{"key": str(i), "e": i, "px": i, "det": .9} for i in range(12)]
        embeddings = np.array([[i, 0] for i in range(12)])
        picked = name_sheet.pick_crops(members, embeddings)
        self.assertEqual({k for k, flag in picked if flag}, {"0", "1", "10", "11"})
        self.assertEqual(len(picked), len(set(k for k, _ in picked)))

    def test_coverage(self):
        self.assertEqual(name_sheet.coverage([3, 2, 4]), [3, 5, 9])
        self.assertEqual(name_sheet.coverage([]), [])

    def test_fingerprint(self):
        self.assertEqual(name_sheet.fingerprint({"1": ["b", "a"]}), name_sheet.fingerprint({"1": ["a", "b"]}))
        self.assertNotEqual(name_sheet.fingerprint({"1": ["a"]}), name_sheet.fingerprint({"1": ["a", "b"]}))

    def test_generate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = fixture(root)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                name_sheet.main(["--data", str(data), "--out", str(root / "out")])
            page = (root / "out/index.html").read_text(encoding="utf-8")
            for value in ('id="cluster-1"', 'id="cluster-2"', 'href="sheet.css?v=', 'src="sheet.js?v=', 'role="combobox"', 'data-action="new"'):
                self.assertTrue(value in page)
            self.assertNotIn('<datalist', page)
            for asset in ('sheet.css', 'sheet.js'):
                self.assertEqual((root / 'out' / asset).read_bytes(), (Path(name_sheet.__file__).parent / 'sheet_assets' / asset).read_bytes())
            cfg = json.loads(re.search(r'<script>window.SHEET = (.*?)</script>', page).group(1))
            self.assertEqual(cfg['clusters'], ['1', '2'])
            self.assertEqual(cfg['fingerprint'], name_sheet.fingerprint(name_sheet.cluster_members(name_sheet.load(data / 'faces.json'))))
            self.assertTrue(cfg['people'][0]['id'] == 'S0001')
            self.assertFalse(cfg['people'][0]['avatar'])
            self.assertEqual(page.count('tabindex="0" role="group"'), 2)
            self.assertNotIn("Test One", buf.getvalue())
            self.assertNotIn("S0001", buf.getvalue())
            for crop in (root / "out/crops").glob("*.jpg"):
                with Image.open(crop) as image:
                    self.assertEqual(image.size, (128, 128))

    def test_people_and_escaping(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = fixture(root)
            meta = {"S0001": {"name": "Zeta </script>", "kind": "student", "batch": "MB28", "reg_no": "REG42"}, "S0002": {"name": "Alpha", "kind": "student"}, "T0001": {"name": "Beta", "kind": "staff"}}
            write(data / 'people_meta.json', meta)
            write(data / 'questions.json', [{"type": "cluster", "cluster": c, "cands": list(meta)} for c in (1, 2)])
            (root / 'img/people').mkdir()
            Image.new('RGB', (10, 10)).save(root / 'img/people/S0002.webp')
            (root / 'out/people').mkdir(parents=True)
            Image.new('RGB', (10, 10)).save(root / 'out/people/T0001.jpg')
            with contextlib.redirect_stdout(io.StringIO()):
                name_sheet.generate(data, root / 'out')
            page = (root / 'out/index.html').read_text(encoding='utf-8')
            payload = re.search(r'<script>window.SHEET = (.*?)</script>', page).group(1)
            self.assertNotIn('</', payload)
            self.assertIn(r'<\/script>', payload)
            cfg = json.loads(payload)
            self.assertTrue([p['id'] for p in cfg['people']] == ['S0002', 'S0001', 'T0001'])
            self.assertEqual([p['avatar'] for p in cfg['people']], [True, False, True])
            self.assertTrue(cfg['people'][1]['reg'] == 'REG42' and cfg['people'][1]['batch'] == 'MB28')
            for key in (1, 2, 3):
                self.assertEqual(page.count(f'data-key="{key}"'), 2)

    @unittest.skipUnless(shutil.which('node'), 'node is not installed')
    def test_javascript_syntax(self):
        asset = Path(name_sheet.__file__).parent / 'sheet_assets/sheet.js'
        result = subprocess.run(['node', '--check', str(asset)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'node is not installed')
    def test_filter_logic(self):
        if subprocess.run(['node', '-e', "require('vm')"], capture_output=True).returncode:
            self.skipTest('node vm is unavailable')
        asset = Path(name_sheet.__file__).parent / 'sheet_assets/sheet.js'
        script = r"""
const fs=require('fs'), vm=require('vm'), assert=require('assert');
const ctx={module:{exports:{}}}; vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),ctx);
const f=ctx.module.exports.filterPeople;
const people=Array.from({length:12},(_,i)=>({id:'S'+(1000+i),name:'Invented '+i,reg:'REG'+i}));
assert.equal(f(people,'INVENTED').length,8);
assert.equal(f(people,'s1009')[0].id,'S1009');
assert.equal(f(people,'reg11')[0].id,'S1011');
assert.equal(f(people,'1009').some(p=>p.newPerson),false);
for(const t of ['9999','s9999','S999999']) {const r=f(people,t);assert.equal(r.at(-1).id,'S'+t.replace(/^s/i,''));assert(r.at(-1).newPerson);}
for(const t of ['123','1234567','other']) assert.equal(f(people,t).some(p=>p.newPerson),false);
assert.equal(f(people,'').length,8);
"""
        result = subprocess.run(['node', '-e', script, str(asset)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
