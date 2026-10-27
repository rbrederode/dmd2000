import numpy as np
from astropy.io import fits
from astropy.wcs import WCS

from util import hi4pi


def test_get_cube_uses_car_projection_directory_and_reuses_local_copy(tmp_path, monkeypatch):
    cached = tmp_path / 'download.fits'
    cached.write_bytes(b'FITS download')
    requested = []

    def download(url, **kwargs):
        requested.append(url)
        return str(cached)

    monkeypatch.setattr(hi4pi, 'download_file', download)
    directory = tmp_path / 'cubes'

    path = hi4pi.get_cube('CAR_E02.fits', directory)
    assert path == directory / 'CAR_E02.fits'
    assert requested == ['https://cdsarc.cds.unistra.fr/ftp/J/A+A/594/A116/CUBES/GAL/CAR/CAR_E02.fits']
    assert path.read_bytes() == cached.read_bytes()
    assert hi4pi.get_cube('CAR_E02.fits', directory) == path
    assert len(requested) == 1


def test_extract_recovers_constant_brightness_and_radio_velocity_axis(tmp_path):
    wcs = WCS(naxis=3)
    wcs.wcs.ctype = ['GLON-CAR', 'GLAT-CAR', 'VRAD']
    wcs.wcs.cunit = ['deg', 'deg', 'm/s']
    wcs.wcs.crval = [30, 0, -2000]
    wcs.wcs.crpix = [11, 11, 1]
    wcs.wcs.cdelt = [-0.5, 0.5, 1000]
    header = wcs.to_header()
    header['BUNIT'] = 'K'
    data = np.broadcast_to(np.array([5., 10., 15.])[:, None, None], (3, 21, 21)).copy()
    path = tmp_path / 'cube.fits'
    fits.PrimaryHDU(data, header).writeto(path)

    velocity, temperature = hi4pi.extract(path, 30, 0, 1.0)

    np.testing.assert_allclose(velocity, [-2, -1, 0])
    np.testing.assert_allclose(temperature, [5, 10, 15])
