import numpy as np
import pytest
from types import SimpleNamespace
from astropy.io import fits
from astropy.table import Table
from astropy.wcs import WCS
from requests.exceptions import HTTPError, Timeout

from sdk.util import hi4pi


def catalogue():
    return [Table(rows=[(90., 0., 'CAR', 'near.fits'),
                        (100., 0., 'CAR', 'next.fits'),
                        (90., 0., 'AIT', 'other_projection.fits'),
                        (180., 0., 'CAR', 'far.fits')],
                  names=['GLON', 'GLAT', 'WCSproj', 'FileName'])]


def mock_vizier(monkeypatch, responses, primary='vizier.cds.unistra.fr'):
    attempted = []
    viz = SimpleNamespace(VIZIER_SERVER=primary)

    def get_catalogs(name):
        assert name == hi4pi.CATALOG
        attempted.append(viz.VIZIER_SERVER)
        response = responses[viz.VIZIER_SERVER]
        if isinstance(response, Exception):
            raise response
        return response

    def create(**kwargs):
        assert kwargs['row_limit'] == -1
        assert kwargs['timeout'] == 30
        return viz

    viz.get_catalogs = get_catalogs
    monkeypatch.setattr(hi4pi, 'Vizier', create)
    return attempted


def test_catalogue_uses_primary_without_querying_mirrors(monkeypatch):
    attempted = mock_vizier(monkeypatch, {'vizier.cds.unistra.fr': catalogue()})
    assert hi4pi.catalog_tiles(90, 0, 2) == ['near.fits', 'next.fits']
    assert attempted == ['vizier.cds.unistra.fr']


@pytest.mark.parametrize('failure', [HTTPError('404 Not Found'), Timeout('timed out'),
                                    hi4pi.TableParseError('invalid VOTable'), [],
                                    [Table(names=['GLON'])],
                                    [Table({'unexpected': [1]})]])
def test_catalogue_falls_back_after_request_or_table_failure(monkeypatch, failure):
    attempted = mock_vizier(monkeypatch, {'vizier.cds.unistra.fr': failure,
                                        hi4pi.CATALOG_MIRRORS[0]: catalogue()})
    assert hi4pi.catalog_tiles(90, 0, 2) == ['near.fits', 'next.fits']
    assert attempted == ['vizier.cds.unistra.fr', hi4pi.CATALOG_MIRRORS[0]]


def test_catalogue_tries_second_mirror(monkeypatch):
    attempted = mock_vizier(monkeypatch, {'vizier.cds.unistra.fr': HTTPError('404'),
                                        hi4pi.CATALOG_MIRRORS[0]: Timeout('timeout'),
                                        hi4pi.CATALOG_MIRRORS[1]: catalogue()})
    assert hi4pi.catalog_tiles(90, 0, 2) == ['near.fits', 'next.fits']
    assert attempted == ['vizier.cds.unistra.fr', *hi4pi.CATALOG_MIRRORS]


def test_catalogue_reports_all_server_failures(monkeypatch):
    servers = ['vizier.cds.unistra.fr', *hi4pi.CATALOG_MIRRORS]
    mock_vizier(monkeypatch, {server: HTTPError('404') for server in servers})
    with pytest.raises(RuntimeError, match='all servers') as error:
        hi4pi.catalog_tiles(90, 0, 2)
    for server in servers:
        assert server in str(error.value)


def test_configured_mirror_is_not_retried(monkeypatch):
    primary, fallback = hi4pi.CATALOG_MIRRORS
    attempted = mock_vizier(monkeypatch, {primary: HTTPError('404'), fallback: catalogue()}, primary)
    assert hi4pi.catalog_tiles(90, 0, 2) == ['near.fits', 'next.fits']
    assert attempted == [primary, fallback]


def test_no_nearby_tiles_does_not_trigger_server_fallback(monkeypatch):
    attempted = mock_vizier(monkeypatch, {'vizier.cds.unistra.fr': catalogue()})
    with pytest.raises(RuntimeError, match='No nearby CAR tiles'):
        hi4pi.catalog_tiles(270, 0, 2)
    assert attempted == ['vizier.cds.unistra.fr']


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
