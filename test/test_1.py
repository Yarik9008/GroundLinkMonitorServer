import json
import math
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import matplotlib.pyplot as plt


def load_html(url: str) -> str:
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return response.text


def save_html(path: str, html: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)


def parse_station_passes(html: str) -> dict[str, list[str]]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if table is None:
        return {}

    rows = table.find_all("tr")
    if not rows:
        return {}

    header_cells = rows[0].find_all("th")
    station_names = []
    for th in header_cells[1:]:
        link = th.find("a")
        station_names.append(link.get_text(strip=True) if link else th.get_text(strip=True))

    station_passes: dict[str, list[str]] = {name: [] for name in station_names}

    for row in rows[1:]:
        cells = row.find_all("td")
        if not cells:
            continue
        date = cells[0].get_text(strip=True)
        for idx, cell in enumerate(cells[1:]):
            if idx >= len(station_names):
                break
            station = station_names[idx]
            cell_text = cell.get_text(separator="\n", strip=True)
            for line in cell_text.splitlines():
                if line:
                    station_passes[station].append(f"{date} {line}".strip())

    return station_passes


def find_first_log_view_url(html: str, base_url: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if "log_view/" in href and "_rec.log" in href:
            return urljoin(base_url, href)
    return None


def fetch_log_view(url: str) -> str:
    response = requests.get(url, timeout=30, verify=False)
    response.raise_for_status()
    return response.text


def download_log(url: str, path: str) -> None:
    response = requests.get(url, timeout=30, verify=False)
    response.raise_for_status()
    with open(path, "wb") as f:
        f.write(response.content)


def download_log_get(base_url: str, log_name: str, output_path: str | None = None) -> str:
    url = urljoin(base_url, f"log_get/{log_name}")
    output = output_path or log_name
    response = requests.get(url, timeout=30, verify=False)
    response.raise_for_status()
    with open(output, "wb") as f:
        f.write(response.content)
    return output


def plot_polar_track_from_json(json_path: str, output_path: str | None = None, show: bool = False) -> None:
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    table = data.get("table") or []
    if not table:
        raise ValueError("track_table JSON has no table data")

    min_el = data.get("el_min", 0)
    if min_el is None:
        min_el = 0

    az = [math.radians(d["az"]) for d in table]
    r = [90 - d["el"] for d in table]

    rssi_values = [d.get("rssi", 0) for d in table]
    rssi_min = min(rssi_values)
    rssi_max = max(rssi_values)

    fig = plt.figure(figsize=(7, 7))
    ax = fig.add_subplot(111, projection="polar")
    ax.set_theta_zero_location("N")
    ax.set_theta_direction(-1)
    ax.set_rmax(90 - min_el)

    ax.plot(az, r, color="#cccccc", linewidth=2)

    norm = plt.Normalize(rssi_min, rssi_max)
    colors = plt.cm.viridis(norm(rssi_values))
    ax.scatter(az, r, c=colors, s=30, edgecolors="none")

    ax.set_title("Track view (az/el)")
    ax.grid(True)

    if output_path:
        fig.savefig(output_path, dpi=150, bbox_inches="tight")

    if show:
        plt.show()
    else:
        plt.close(fig)


def parse_log_file(path: str) -> list[dict[str, float | str]]:
    points: list[dict[str, float | str]] = []
    header_map: dict[str, int] = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if line.startswith("#Time") or line.startswith("Time"):
                headers = line.lstrip("#").split("\t")
                header_map = {name: idx for idx, name in enumerate(headers)}
                continue
            if line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 4:
                continue
            try:
                time_str = parts[0].split(" ")[-1].split(".")[0]
                az_idx = header_map.get("Az", 1)
                el_idx = header_map.get("El", 2)
                level_idx = header_map.get("Level", 3)
                snr_idx = header_map.get("SNR", 4)
                az = float(parts[az_idx])
                el = float(parts[el_idx])
                level = float(parts[level_idx])
                snr = float(parts[snr_idx]) if snr_idx < len(parts) else 0.0
            except ValueError:
                continue
            points.append(
                {"time": time_str, "az": az, "el": el, "level": level, "snr": snr}
            )
    return points


def plot_polar_track_from_log(log_path: str, output_path: str | None = None, show: bool = False) -> None:
    points = parse_log_file(log_path)
    if not points:
        raise ValueError("log file has no data points")

    n = len(points)
    az = [math.radians(p["az"]) for p in points]
    r = [90 - p["el"] for p in points]
    times = [p["time"] for p in points]
    levels = [p["level"] for p in points]

    min_el = min(p["el"] for p in points)
    base_level = min(levels)

    fig = plt.figure(figsize=(7.5, 7.5))
    ax = fig.add_subplot(111, projection="polar")
    ax.set_theta_zero_location("N")
    ax.set_theta_direction(-1)
    ax.set_rmax(90 - min_el)

    ax.plot(az, r, color="#bdbdbd", linewidth=2)

    elev_ticks = [v for v in range(20, 100, 10) if v >= int(min_el)]
    r_ticks = [90 - v for v in elev_ticks]
    ax.set_yticks(r_ticks)
    ax.set_yticklabels([str(v) for v in elev_ticks])
    ax.set_xticks([math.radians(v) for v in range(0, 360, 30)])
    ax.set_xticklabels([f"{v}$^\\circ$" for v in range(0, 360, 30)])

    ax.grid(True, linestyle=(0, (1, 3)), color="#9a9a9a", linewidth=1)

    timestep = 10
    if n > 100:
        timestep = 10 * (n // 100)

    time_idx = list(range(0, n, timestep))
    ax.scatter(
        [az[i] for i in time_idx],
        [r[i] for i in time_idx],
        c="#cccccc",
        s=20,
        edgecolors="none",
    )
    for i in time_idx:
        ax.annotate(
            times[i],
            xy=(az[i], r[i]),
            xytext=(12, 8),
            textcoords="offset points",
            fontsize=8,
            ha="left",
            va="center",
        )

    signal_step = n // 40 + 1
    signal_idx = list(range(0, n, signal_step))
    signal_values = [levels[i] - base_level for i in signal_idx]
    signal_levels = [int(levels[i] - base_level) for i in signal_idx]

    from matplotlib.colors import LinearSegmentedColormap

    domain = [0, 3, 6, 6, 9, 12, 16, 24, 34]
    colors = ["grey", "grey", "red", "red", "yellow", "green", "blue", "violet", "#ebdef0"]
    max_domain = max(domain)
    positions = [d / max_domain for d in domain]
    cmap = LinearSegmentedColormap.from_list("signal", list(zip(positions, colors)))

    norm = plt.Normalize(0, max_domain)
    ax.scatter(
        [az[i] for i in signal_idx],
        [r[i] for i in signal_idx],
        c=[cmap(norm(v)) for v in signal_values],
        s=55,
        edgecolors="none",
    )

    for j, i in enumerate(signal_idx):
        if j % 4 == 0:
            ax.annotate(
                str(signal_levels[j]),
                xy=(az[i], r[i]),
                xytext=(-20, 4),
                textcoords="offset points",
                fontsize=8,
                ha="left",
                va="center",
            )

    ax.set_title(log_path, fontsize=10, loc="left", pad=15, color="#5b2aa1")

    if output_path:
        fig.savefig(output_path, dpi=150, bbox_inches="tight")

    if show:
        plt.show()
    else:
        plt.close(fig)


if __name__ == "__main__":
    base_url = "http://eus.lorett.org/eus/logs_list.html"
    html = load_html(base_url)
    save_html("loglist_frames.html", html)
    print(f"Saved HTML size: {len(html)} bytes")

    passes_by_station = parse_station_passes(html)
    for station, passes in passes_by_station.items():
        print(station)
        for entry in passes:
            print(f"  {entry}")

    plot_polar_track_from_log("PM.log", output_path="PM.png")
    print("Saved plot: PM.png")

    log_view_url = "https://eus.lorett.org/eus/log_view/R2M6_AANII_01__20260109_154718_FENGYUN_3E_rec.log"
    log_view_html = fetch_log_view(log_view_url)
    save_html("R2M6_AANII_01__20260109_154718_FENGYUN_3E_rec.log.html", log_view_html)
    print("Saved log view HTML: R2M6_AANII_01__20260109_154718_FENGYUN_3E_rec.log.html")
    download_log_get("https://eus.lorett.org/eus/", "R2M6_AANII_01__20260109_154718_FENGYUN_3E_rec.log")
    print("Downloaded log file: R2M6_AANII_01__20260109_154718_FENGYUN_3E_rec.log")

