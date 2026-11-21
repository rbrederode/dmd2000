from astroquery.vizier import Vizier
from astroquery.simbad import Simbad
from astropy.coordinates import SkyCoord
import astropy.units as u
from astropy.table import Table
from astropy.table import conf

from astroquery.simbad import Simbad
from pyvo.dal import TAPService
from astropy.table import Table

def query_simbad_columns():
  tap = TAPService("https://simbad.u-strasbg.fr/simbad/sim-tap")

  # First, let's see what tables are available
  print("Available tables:")
  tables = tap.tables
  for table_name in tables.keys():
    print(f"  {table_name}")
    table = tables[table_name]
    if hasattr(table, 'columns'):
      print(f"    Columns: {', '.join([col.name for col in table.columns[:10]])}")  # First 10 columns

def query_simbad_catalogs():
  tap = TAPService("https://simbad.u-strasbg.fr/simbad/sim-tap")
  
  # Query the cat table which contains catalog information
  adql = """
  SELECT TOP 10000
      cat_name,
      description,
      "size"
  FROM cat
  WHERE "size" > 100
  ORDER BY "size" DESC
  """

  result = tap.search(adql).to_table()
  return result
  
def query_optically_brightest_stars():
    tap = TAPService("https://simbad.u-strasbg.fr/simbad/sim-tap")

    # ADQL: get top 1000 stars by V magnitude
    adql = """
    SELECT TOP 1000
        b.main_id,
        b.otype_txt,
        b.ra,
        b.dec,
        f.flux
    FROM basic AS b
    JOIN flux AS f ON f.oidref = b.oid
    WHERE f.filter = 'V'
      AND f.flux IS NOT NULL
      AND b.otype_txt = 'Star'
    ORDER BY f.flux ASC
    """

    result = tap.search(adql).to_table()
    print(result)

# ---------------------------
# Query NVSS catalog
# ---------------------------
def query_nvss_vizier(top_n=50):
    Vizier.ROW_LIMIT = 1000000
    catalog = 'VIII/65/nvss'
    v = Vizier(columns=['RAJ2000', 'DEJ2000', 'S1.4'], column_filters={"S1.4":">0"})
    tbl = v.query_constraints(catalog=catalog, DEC="> -30")
    t = tbl[0]
    t.sort('S1.4', reverse=True)
    return t[:top_n]

# ---------------------------
# Cross-match NVSS with SIMBAD
# ---------------------------
def crossmatch_nvss_simbad(nvss_table, radius_arcsec=5.0):
    # Add desired SIMBAD fields
    Simbad.reset_votable_fields()
    Simbad.add_votable_fields('main_id', 'otype_txt', 'ra', 'dec')

    results = []
    for row in nvss_table:
        ra = row['RAJ2000']
        dec = row['DEJ2000']
        flux = row['S1.4']

        coord = SkyCoord(ra, dec, unit=(u.hourangle, u.deg))
        simbad_tbl = Simbad.query_region(coord, radius=radius_arcsec*u.arcsec)

        if simbad_tbl is None or len(simbad_tbl) == 0:
            results.append((ra, dec, flux, None, None))
        else:
            simbad_row = simbad_tbl[0]
            results.append((
                ra, dec, flux,
                simbad_row['main_id'],    # lowercase
                simbad_row['otype_txt']   # lowercase
            ))

    return Table(rows=results, names=['RA_NVSS', 'Dec_NVSS', 'Flux_mJy', 'Simbad_name', 'Simbad_type'])

# ---------------------------
# Main execution
# ---------------------------
if __name__ == "__main__":

    print("Columns in SIMBAD 'basic' table:")
    query_simbad_columns()

    print("Querying SIMBAD catalogs...")
    catalogs = query_simbad_catalogs()
    catalogs.write("simbad_catalogs.csv", format="csv", overwrite=True)
    exit()

    print("Querying optically brightest stars from SIMBAD...")
    query_optically_brightest_stars()
    exit()


    nvss_top = query_nvss_vizier(top_n=50)
    print(f"Retrieved {len(nvss_top)} NVSS sources.")

    print("Cross-matching with SIMBAD...")
    matched_table = crossmatch_nvss_simbad(nvss_top, radius_arcsec=5.0)
    conf.row_print_max = len(matched_table)  # show all rows
    print(matched_table)

    matched_table.write("nvss_simbad_crossmatch.csv", format="csv", overwrite=True)
    print("Saved crossmatch results.")
