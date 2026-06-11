import zipfile, netCDF4 as nc, numpy as np

with zipfile.ZipFile("era5_download.nc") as zf:
    members = zf.namelist()
    print(f"ZIP contents ({len(members)} files):")
    for m in members:
        print(f"  {m}")
    
    # Extract and probe all nc files
    for m in members:
        if not m.endswith(".nc"):
            continue
        zf.extract(m, "era5_extract")
        path = f"era5_extract/{m}"
        try:
            ds = nc.Dataset(path)
            print(f"\n--- {m} ---")
            print(f"  Variables: {list(ds.variables.keys())}")
            for var in ds.variables:
                v = ds.variables[var]
                if v.ndim >= 3:
                    data = v[:]
                    print(f"  {var:<10} shape={v.shape}  "
                          f"units={getattr(v,'units','?'):<20}  "
                          f"range=[{float(np.ma.min(data)):.6f}, {float(np.ma.max(data)):.6f}]")
            ds.close()
        except Exception as e:
            print(f"  ERROR: {e}")
