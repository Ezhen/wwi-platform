import netCDF4 as nc
import numpy as np
import zipfile

NC_FILE = "era5_download.nc"

actual_nc = NC_FILE
with open(NC_FILE, "rb") as f:
    if f.read(2) == b"PK":
        with zipfile.ZipFile(NC_FILE) as zf:
            members = [m for m in zf.namelist() if m.endswith(".nc")]
            zf.extract(members[0], "era5_extract")
            actual_nc = f"era5_extract/{members[0]}"
        print(f"Extracted: {actual_nc}")

ds = nc.Dataset(actual_nc)
print(f"\nDimensions: {list(ds.dimensions.keys())}")
print(f"Variables:  {list(ds.variables.keys())}\n")

for name, var in ds.variables.items():
    try:
        data = var[:]
        if data.ndim == 0:  # scalar
            print(f"  {name:<20} scalar={float(data):.4f}  units={getattr(var,'units','?')}")
        else:
            print(f"  {name:<20} shape={str(var.shape):<20} "
                  f"units={getattr(var,'units','?'):<25} "
                  f"range=[{float(np.ma.min(data)):.4f}, {float(np.ma.max(data)):.4f}]")
            if data.ndim == 1 and len(data) <= 20:
                print(f"    values: {list(data[:])}")
    except Exception as e:
        print(f"  {name:<20} ERROR: {e}")

# Decode valid_time
if "valid_time" in ds.variables:
    vt = ds.variables["valid_time"]
    times = nc.num2date(vt[:], units=vt.units)
    print(f"\nTime range: {times[0]} → {times[-1]}  ({len(times)} steps)")

ds.close()
