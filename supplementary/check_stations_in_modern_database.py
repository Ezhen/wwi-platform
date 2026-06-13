from pathlib import Path
import sqlite3

ROOT       = Path(__file__).resolve().parent
con = sqlite3.connect(ROOT / 'export/databases/spw_liege.db')
for r in con.execute('''
    SELECT station_no, station_name, river_name, lat, lon
    FROM stations
    WHERE river_name LIKE \"%Ourthe%\"
       OR river_name LIKE \"%Vesdre%\"
       OR river_name LIKE \"%Amblève%\"
       OR river_name LIKE \"%Meuse%\"
       OR river_name LIKE \"%Salm%\"
    ORDER BY river_name, lat ASC
'''):
    print(r)
con.close()
