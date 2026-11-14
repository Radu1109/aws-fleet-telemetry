"""Build a Lambda deployment zip from an installed package directory."""

import os
import sys
import zipfile

src_dir, out_zip = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
    for root, _, files in os.walk(src_dir):
        for f in files:
            full = os.path.join(root, f)
            zf.write(full, os.path.relpath(full, src_dir))
