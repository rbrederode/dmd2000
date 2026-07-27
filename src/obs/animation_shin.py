from datetime import datetime
import os
import glob
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.animation as animation
import scipy.constants

from astropy.time import Time
from astropy.coordinates import SkyCoord, EarthLocation
from astropy.units import deg, m

from starplot import ZenithPlot, Observer, _
from starplot.styles import PlotStyle, extensions


# ============================================
# Constants
# ============================================
CVEL = scipy.constants.c          # Speed of light [m/s]
f0 = 1420.405e6                   # HI 21cm rest frequency [Hz]

# Observation site (Alderley Edge)
lon = -2.308576498655725
lat = 53.236539429110486
h = 77.0

obs_loc = EarthLocation(lon=lon * deg, lat=lat * deg, height=h * m)

# ============================================
# File Handling
# ============================================
def load_observation_files(pattern="UT*.csv"):
    """Load and sort observation CSV files."""
    obs_list = glob.glob(pattern)
    obs_list.sort()
    return obs_list


def parse_filename(filename):
    """Extract timestamp, azimuth, elevation, and Vlsr from filename."""
    t = filename.split("_")[0]
    az = int(filename.split("Az")[1].split("El")[0])
    el = int(filename.split("El")[1].split("vlsr")[0])
    vlsr = int(filename.split("vlsr")[1].split(".csv")[0])
    return t, az, el, vlsr


# ============================================
# Single-file Processing
# ============================================
def process_file(filename, obs_loc, vlsr0, ch_cut=5, ch_off=20):
    """Process one observation file and remove linear baseline."""
    t, az, el, vlsr = parse_filename(filename)
    v = vlsr0 + vlsr

    ut = Time(
        t[2:6] + "-" + t[6:8] + "-" + t[8:11] +
        t[11:13] + ":" + t[13:15] + ":" + t[15:17],
        format="fits",
    )

    azel = SkyCoord(az, el, frame="altaz", unit="deg",
                    obstime=ut, location=obs_loc)
    lb = azel.transform_to("galactic").to_string("decimal")
    l, b = map(float, lb.split())

    data = np.loadtxt(filename, delimiter=",")
    pt, mt = data[:, 0], data[:, 1]

    pm = (pt - mt) / mt
    mp = (mt - pt) / pt
    sp = (pm[:128] + mp[128:]) / 2

    y = np.concatenate([sp[ch_cut:ch_off], sp[128 - ch_off:-ch_cut]])
    x = np.concatenate([v[ch_cut:ch_off], v[128 - ch_off:-ch_cut]])

    w = np.polyfit(x, y, 1)
    sp_corrected = sp - (w[0] * v + w[1])

    return {
        "HI": sp_corrected,
        "LB": ut.value[:-4] + f"UT,  (l,b) = ({l:.1f},{b:.1f})",
        "L": l,
        "B": b,
        "Vlsr": v,
        "UT": ut,
        "az": az,
        "el": el,
        "t": t,
    }


# ============================================
# Baseline Correction
# ============================================
def baseline_correction(HI, ns0=6, ne0=122, nb_max=20, nb_min=90, n_ave=3):
    """Quadratic + linear baseline correction for all spectra."""
    xx = np.arange(128)

    hi0 = HI[np.argmin([np.max(h) for h in HI])]

    y_min = np.concatenate([hi0[ns0:nb_max], hi0[nb_min:ne0]])
    x_min = np.concatenate([xx[ns0:nb_max], xx[nb_min:ne0]])
    w2 = np.polyfit(x_min, y_min, 2)
    base = w2[0] * xx**2 + w2[1] * xx + w2[2]

    HI_bl = []
    for hi in HI:
        hi_corr = hi - base
        y = np.concatenate([hi_corr[ns0:ns0 + n_ave],
                            hi_corr[ne0 - n_ave:ne0]])
        x = np.concatenate([xx[ns0:ns0 + n_ave],
                            xx[ne0 - n_ave:ne0]])
        w = np.polyfit(x, y, 1)
        HI_bl.append(hi_corr - (w[0] * xx + w[1]))

    return np.array(HI_bl)


# ============================================
# Star Chart Generation
# ============================================
def generate_star_chart(dt, lon, lat, ra, dec, filename="star_chart_basic.png"):
    """Generate a zenith star chart marking the pointing direction."""
    observer = Observer(dt=dt, lon=lon, lat=lat)

    p = ZenithPlot(
        observer=observer,
        style=PlotStyle().extend(
            extensions.BLUE_GOLD,
            extensions.GRADIENT_PRE_DAWN,
            {"milky_way": {"alpha": 0.36, "color": "#FFFFFF"}},
        ),
        resolution=1800,
        autoscale=True,
    )

    p.marker(
        ra=ra,
        dec=dec,
        style={
            "marker": {
                "size": 100,
                "symbol": "circle_cross",
                "fill": "none",
                "color": "yellow",
                "edge_width": 5,
                "alpha": 1,
            },
        },
    )

    p.horizon()
    p.constellations()
    p.stars(where=[_.magnitude < 3], where_labels=[False])
    p.milky_way()

    p.export(filename, transparent=True, padding=0.1)
    p.close_fig()

    return plt.imread(filename)


# ============================================
# Animation
# ============================================
def create_animation(obs_list, HI_bl, Vlsr, LB, ns0=3, ne0=125):
    """Create animation of star chart + HI spectrum."""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 5))
    ims = []

    for i, f in enumerate(obs_list):
        print(f"{i+1}/{len(obs_list)}")

        t, az, el, _ = parse_filename(f)

        ut = Time(
            t[2:6] + "-" + t[6:8] + "-" + t[8:11] +
            t[11:13] + ":" + t[13:15] + ":" + t[15:17],
            format="fits",
        )

        dt = datetime.fromisoformat(
            t[2:6] + "-" + t[6:8] + "-" + t[8:11] +
            t[11:13] + ":" + t[13:15] + ":" + t[15:17] + "Z"
        )

        azel = SkyCoord(az, el, frame="altaz", unit="deg",
                        obstime=ut, location=obs_loc)
        RaDec = azel.transform_to("icrs").to_string("decimal")
        ra, dec = map(float, RaDec.split())

        img = generate_star_chart(dt, lon, lat, ra, dec)

        im1 = ax1.imshow(img)
        ax1.axis("off")
        ttl = ax1.text(
            1.5, 1.1, LB[i],
            ha="center", va="top",
            transform=ax1.transAxes,
            fontsize="large",
        )

        im2 = ax2.plot(Vlsr[i][ns0:ne0], HI_bl[i][ns0:ne0], c="b")
        ax2.set_xlim([-125, 125])
        ax2.set_xlabel("V$_{lsr}$ [km/s]")
        ax2.set_ylabel("Intensity [a.u.]")

        plt.subplots_adjust(wspace=0.3)
        ims.append([im1] + [ttl] + im2)

    anim = animation.ArtistAnimation(fig, ims, interval=50, blit=True)
    anim.save("AzEl.gif", writer="pillow")

    fig.clf()
    if os.path.exists("star_chart_basic.png"):
        os.remove("star_chart_basic.png")


# ============================================
# Main
# ============================================
if __name__ == "__main__":
    obs_list = load_observation_files()

    v0 = 2.4e6 / 256 / f0 * CVEL / 1e3
    vlsr0 = np.array([(64 - i) * v0 for i in range(128)]) - v0 / 2

    results = [process_file(f, obs_loc, vlsr0) for f in obs_list]

    HI = np.array([r["HI"] for r in results])
    Vlsr = np.array([r["Vlsr"] for r in results])
    LB = [r["LB"] for r in results]

    HI_bl = baseline_correction(HI)
    create_animation(obs_list, HI_bl, Vlsr, LB)