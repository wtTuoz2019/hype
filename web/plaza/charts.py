def pnl_svg(points, width=720, height=200) -> str:
    series = []
    for item in points or []:
        try:
            series.append(float(item[1]))
        except (TypeError, ValueError, IndexError):
            continue
    if len(series) < 2:
        return ""
    low = min(series)
    high = max(series)
    span = high - low or 1.0
    coords = []
    last = len(series) - 1
    for index, value in enumerate(series):
        x = 8 + index * (width - 16) / last
        y = height - 12 - (value - low) / span * (height - 24)
        coords.append(f"{x:.1f},{y:.1f}")
    color = "#0ecb81" if series[-1] >= series[0] else "#f6465d"
    line = " ".join(coords)
    area = f"8,{height - 8} " + line + f" {width - 8:.1f},{height - 8}"
    return (
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img">'
        f'<polygon points="{area}" fill="{color}" opacity="0.16"></polygon>'
        f'<polyline points="{line}" fill="none" stroke="{color}" stroke-width="2"></polyline>'
        f"</svg>"
    )
