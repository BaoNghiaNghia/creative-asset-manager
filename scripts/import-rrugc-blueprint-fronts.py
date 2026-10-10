from pathlib import Path
from zipfile import ZipFile
import hashlib

source = Path('blueprint-8869-fronts-source.zip')
target = Path('apps/client/public/rrugc/blueprint/fronts')
assert source.exists(), 'Original 12-color ZIP missing'
expected = ['black','brown','camo-green','charcoal','forest-green','khaki','maroon','mossy-oak-breakup','navy','realtree-all-purpose','red','royal']
target.mkdir(parents=True,exist_ok=True)
with ZipFile(source) as archive:
    files = [name for name in archive.namelist() if name.lower().endswith('.jpg')]
    assert len(files)==12, f'Expected 12 original fronts, received {len(files)}'
    for color in expected:
        found = [name for name in files if name.lower().split('/')[-1].startswith('8869-natural-'+color+'-01-front')]
        assert len(found)==1,(color,found)
        data = archive.read(found[0])
        assert data[:3]==b'\xff\xd8\xff'
        path = target/(color+'.jpg')
        path.write_bytes(data)
        print(str(path),len(data),hashlib.sha256(data).hexdigest()[:12])
