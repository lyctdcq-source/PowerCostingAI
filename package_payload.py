from pathlib import Path
import zipfile

root = Path(__file__).resolve().parent
app = root / 'dist' / 'PowerCostAgent'
with zipfile.ZipFile(root / 'payload.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(app.rglob('*')):
        if path.is_file():
            archive.write(path, str(path.relative_to(app)))
print('Payload ready:', (root/'payload.zip').stat().st_size)
