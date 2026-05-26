# -*- coding: utf-8 -*-
"""
mapview.py — Generic config-driven geospatial dashboard.

All project-specific values live in specs.json.
Swap the JSON and data files to deploy a completely different project
with zero changes to this module.
"""

import json
import tempfile
import requests
import streamlit as st
import folium
import geopandas as gpd
import pandas as pd
import plotly.express as px
from pathlib import Path

# ── Root directory — always the folder containing mapview.py ──────────────────
# Anchors all relative paths in specs.json to the script location,
# so `streamlit run` works from any working directory.
ROOT_DIR = Path(__file__).parent.resolve()

def _resolve(path_or_url: str) -> str:
    """Returns URLs unchanged; resolves everything else relative to ROOT_DIR."""
    if not path_or_url or path_or_url.startswith(("http://", "https://")):
        return path_or_url
    return str(ROOT_DIR / path_or_url)

# ── Constants ─────────────────────────────────────────────────────────────────

SPECS_PATH = str(ROOT_DIR / "specs.json")
FALLBACK_COLOR = "#808080"
FALLBACK_CENTER = [-15.0, -50.0]
FALLBACK_ZOOM = 4

REQUIRED_KEYS = [
    "title_page", "page_icon", "title_intro", "title_map",
    "title_stats", "title_info", "map_main", "classes",
    "style_classes", "basemaps", "columns", "stats_charts",
]

# ── Config loading ─────────────────────────────────────────────────────────────

@st.cache_data
def load_specs(path: str = SPECS_PATH) -> dict:
    """
    Loads and validates specs.json.
    Halts the app with st.error + st.stop() on any fatal problem.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            specs = json.load(f)
    except FileNotFoundError:
        st.error(f"❌ Arquivo de configuração não encontrado: `{path}`")
        st.stop()
    except json.JSONDecodeError as exc:
        st.error(f"❌ Erro ao interpretar `{path}`: {exc}")
        st.stop()

    missing = [k for k in REQUIRED_KEYS if k not in specs]
    if missing:
        st.error(
            f"❌ Chaves obrigatórias ausentes em `{path}`: "
            + ", ".join(f"`{k}`" for k in missing)
        )
        st.stop()

    return specs

# ── Asset helpers ──────────────────────────────────────────────────────────────

def _is_url(path_or_url: str) -> bool:
    """Returns True for http:// or https:// strings."""
    return path_or_url.startswith("http://") or path_or_url.startswith("https://")


def load_asset_text(path_or_url: str) -> str | None:
    """
    Returns text content from a local file or remote URL.
    Returns None on any failure — never raises.
    """
    if not path_or_url:
        return None
    try:
        if _is_url(path_or_url):
            resp = requests.get(path_or_url, timeout=10)
            resp.raise_for_status()
            return resp.text
        with open(_resolve(path_or_url), "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return None


def load_asset_image(path_or_url: str) -> bytes | str | None:
    """
    Local path → raw bytes.
    Remote URL → URL string (st.image handles URLs natively).
    Returns None on any failure — never raises.
    """
    if not path_or_url:
        return None
    try:
        if _is_url(path_or_url):
            # Verify the URL is reachable; return the string for st.image
            resp = requests.head(path_or_url, timeout=10, allow_redirects=True)
            resp.raise_for_status()
            return path_or_url
        with open(_resolve(path_or_url), "rb") as f:
            return f.read()
    except Exception:
        return None


def render_image_with_fallback(path_or_url: str, caption: str = ""):
    """
    Renders an image from a local path or URL.
    Shows an info message if the asset cannot be loaded.
    """
    if not path_or_url:
        st.info("ℹ️ Nenhuma imagem configurada.")
        return
    asset = load_asset_image(path_or_url)
    if asset is None:
        st.info(f"⚠️ Imagem não encontrada: `{path_or_url}`")
    else:
        st.image(asset, caption=caption, use_container_width=True)

# ── Style helpers ──────────────────────────────────────────────────────────────

def rgba_to_hex(rgba_str: str) -> str:
    """
    Converts "R,G,B,A" (0–255 each) → "#rrggbb".
    Alpha channel is discarded.
    Returns FALLBACK_COLOR on any parse failure — never raises.
    """
    try:
        parts = [int(x.strip()) for x in rgba_str.split(",")]
        r, g, b = parts[0], parts[1], parts[2]
        return f"#{r:02x}{g:02x}{b:02x}"
    except Exception:
        return FALLBACK_COLOR


@st.cache_data
def build_style_lookup(style_classes_json: str) -> dict:
    """
    Accepts json.dumps(specs["style_classes"]) for hashability.
    Returns { class_code: { "color": "#hex", "name": "..." }, ... }
    with all RGBA values pre-converted to hex.
    """
    raw = json.loads(style_classes_json)
    return {
        code: {
            "color": rgba_to_hex(info.get("color", "")),
            "name": info.get("name", code),
        }
        for code, info in raw.items()
    }


def get_feature_color(style_lookup: dict, class_value: str) -> str:
    """
    Returns the hex color for a class value.
    Falls back to FALLBACK_COLOR for any unknown or missing value.
    """
    return style_lookup.get(str(class_value), {}).get("color", FALLBACK_COLOR)

# ── Data loading ───────────────────────────────────────────────────────────────

@st.cache_data
def load_geodata(path_or_url: str | None, simplify_tolerance: float) -> gpd.GeoDataFrame | None:
    """
    Reads a GeoJSON from a local path or URL into a GeoDataFrame.
    Reprojects to EPSG:4326 if needed.
    Applies geometry simplification (preserve_topology=True).
    Returns None on any failure — never raises.
    """
    if not path_or_url:
        return None
    try:
        gdf = gpd.read_file(_resolve(path_or_url))
        if gdf is None or gdf.empty:
            return None
        if gdf.crs and gdf.crs.to_epsg() != 4326:
            gdf = gdf.to_crs(epsg=4326)
        gdf["geometry"] = gdf["geometry"].simplify(
            tolerance=simplify_tolerance, preserve_topology=True
        )
        return gdf
    except Exception:
        return None


@st.cache_data
def load_statistics(path_or_url: str | None) -> pd.DataFrame | None:
    """
    Reads the stats CSV (sep=";").
    Returns None on any failure — never raises.
    """
    if not path_or_url:
        return None
    try:
        if _is_url(path_or_url):
            resp = requests.get(path_or_url, timeout=10)
            resp.raise_for_status()
            from io import StringIO
            return pd.read_csv(StringIO(resp.text), sep=";")
        df = pd.read_csv(_resolve(path_or_url), sep=";")
        if df is None or df.empty:
            return None
        return df
    except Exception:
        return None

# ── Map building ───────────────────────────────────────────────────────────────

def _build_popup_html(columns: dict, properties: dict) -> str:
    """Renders a compact two-column HTML table for a GeoJSON feature popup.
    Floats are truncated to 2 decimal places; other types rendered as-is.
    """
    rows = ""
    for col, label in columns.items():
        value = properties.get(col, "—")
        if isinstance(value, float):
            value = f"{value:.2f}"
        elif value is None:
            value = "—"
        rows += (
            f"<tr>"
            f"<td style='padding:3px 8px 3px 0;font-weight:600;white-space:nowrap'>{label}</td>"
            f"<td style='padding:3px 0'>{value}</td>"
            f"</tr>"
        )
    return f"<table style='font-size:12px;border-collapse:collapse'>{rows}</table>"


@st.cache_resource(show_spinner=False)
def build_map(
    main_geojson_str: str | None,
    roi_geojson_str: str | None,
    style_lookup_json: str,
    specs_json: str,
) -> str:
    """
    Builds and returns a Folium map as a raw HTML string.
    Always returns a valid map — falls back to an empty overview if no data.
    """
    specs = json.loads(specs_json)
    style_lookup = json.loads(style_lookup_json)
    map_opts = specs.get("map_options", {})
    fill_opacity = map_opts.get("fill_opacity", 0.65)
    columns = specs.get("columns", {})
    classes_col = specs.get("classes", "")
    style_roi = specs.get("style_roi", {})
    basemaps = specs.get("basemaps", [])

    # ── Determine center and zoom ──────────────────────────────────────────────
    if main_geojson_str:
        import json as _json
        gj = _json.loads(main_geojson_str)
        coords = []
        for feat in gj.get("features", []):
            geom = feat.get("geometry", {})
            geom_type = geom.get("type", "")
            raw_coords = geom.get("coordinates", [])
            if geom_type == "Polygon":
                for ring in raw_coords:
                    coords.extend(ring)
            elif geom_type == "MultiPolygon":
                for poly in raw_coords:
                    for ring in poly:
                        coords.extend(ring)
        if coords:
            lons = [c[0] for c in coords]
            lats = [c[1] for c in coords]
            center = [(min(lats) + max(lats)) / 2, (min(lons) + max(lons)) / 2]
            bounds = [[min(lats), min(lons)], [max(lats), max(lons)]]
        else:
            center = FALLBACK_CENTER
            bounds = None
    else:
        center = FALLBACK_CENTER
        bounds = None

    # ── Create base map ────────────────────────────────────────────────────────
    m = folium.Map(
        location=center,
        zoom_start=FALLBACK_ZOOM,
        tiles=None,
        prefer_canvas=True,
    )

    # ── Basemap tile layers ────────────────────────────────────────────────────
    for i, bm in enumerate(basemaps):
        folium.TileLayer(
            tiles=bm.get("url", ""),
            attr=bm.get("attribution", ""),
            name=bm.get("name", f"Basemap {i+1}"),
            overlay=False,
            control=True,
        ).add_to(m)

    # ── ROI layer (optional, stroke only) ─────────────────────────────────────
    if roi_geojson_str:
        roi_color = rgba_to_hex(style_roi.get("color", "50,50,50,255"))
        roi_weight = style_roi.get("weight", 2)

        folium.GeoJson(
            roi_geojson_str,
            name=style_roi.get("name", "ROI"),
            style_function=lambda _: {
                "fillOpacity": 0,
                "color": roi_color,
                "weight": roi_weight,
            },
        ).add_to(m)

    # ── Main classified layer ──────────────────────────────────────────────────
    if main_geojson_str:
        # Pre-format float values to 2dp and inject the class name as heading.
        import json as _json
        gj = _json.loads(main_geojson_str)
        for feat in gj.get("features", []):
            props = feat.get("properties", {})
            # Inject human-readable class name for use in popup heading
            code = str(props.get(classes_col, ""))
            props["__class_name__"] = style_lookup.get(code, {}).get("name", code)
            for k, v in props.items():
                if isinstance(v, float):
                    props[k] = f"{v:.2f}"
                elif v is None:
                    props[k] = "—"
        formatted_geojson = _json.dumps(gj)

        # Build popup HTML per feature via on_each_feature JsCode.
        # Shows class name as heading, then only the columns defined in specs
        # (skipping any that are absent from the feature properties).
        col_map = _json.dumps(columns)  # {field: label, ...}
        popup_js = folium.utilities.JsCode(f"""
            function(feature, layer) {{
                var cols = {col_map};
                var props = feature.properties;
                var heading = props.__class_name__ || props["{classes_col}"] || "";
                var rows = "";
                for (var field in cols) {{
                    var val = props[field];
                    if (val === undefined || val === null) continue;
                    rows += "<tr>"
                          + "<td style='padding:3px 8px 3px 0;font-weight:600;white-space:nowrap'>"
                          + cols[field] + "</td>"
                          + "<td style='padding:3px 0'>" + val + "</td>"
                          + "</tr>";
                }}
                var html = "<div style='font-size:12px;max-width:320px'>"
                         + "<div style='font-weight:700;margin-bottom:6px;border-bottom:1px solid #ccc;"
                         + "padding-bottom:4px;white-space:normal;line-height:1.3'>"
                         + heading + "</div>"
                         + "<table style='border-collapse:collapse'>" + rows + "</table>"
                         + "</div>";
                layer.bindPopup(html, {{maxWidth: 340}});
            }}
        """)

        def style_function(feature, _sl=style_lookup, _col=classes_col, _fo=fill_opacity):
            class_val = feature["properties"].get(_col, "")
            return {
                "fillColor": get_feature_color(_sl, class_val),
                "fillOpacity": _fo,
                "color": "none",
                "weight": 0,
            }

        folium.GeoJson(
            formatted_geojson,
            name="Camada principal",
            style_function=style_function,
            on_each_feature=popup_js,
            smooth_factor=1.0,
        ).add_to(m)

        if bounds:
            m.fit_bounds(bounds)

    else:
        # Empty map fallback marker
        folium.Marker(
            location=center,
            popup=folium.Popup("⚠️ Camada principal não encontrada", max_width=250),
            icon=folium.Icon(color="gray", icon="info-sign"),
        ).add_to(m)

    folium.LayerControl().add_to(m)

    return m._repr_html_()

# ── UI renderers ───────────────────────────────────────────────────────────────

def render_sidebar(specs: dict):
    """Renders the sidebar: title + info.md content."""
    st.sidebar.markdown(f"### {specs['title_info']}")
    content = load_asset_text(specs.get("info", ""))
    if content:
        st.sidebar.markdown(content)
    else:
        st.sidebar.info("ℹ️ Arquivo de informações não encontrado.")


def render_intro(specs: dict):
    """Renders the intro section: title + optional description + legend image."""
    st.markdown(f"## {specs['title_intro']}")
    desc = specs.get("desc_intro")
    if desc:
        st.markdown(desc)
    image_path = specs.get("image", "")
    render_image_with_fallback(image_path, caption=specs.get("title_intro", ""))


# Warn the user when the serialised GeoJSON exceeds this threshold (MB).
# Leaflet struggles above ~20 MB of inline GeoJSON in most browsers.
_GEOJSON_WARN_MB = 20

def render_map(specs: dict, style_lookup: dict):
    """Renders the map section. Always shows a map; shows info if main layer unavailable."""
    st.markdown(f"## {specs['title_map']}")
    desc = specs.get("desc_map")
    if desc:
        st.markdown(desc)

    map_opts = specs.get("map_options", {})
    tolerance = map_opts.get("simplify_tolerance", 0.01)

    # Main layer — try to load; None on any failure
    main_path = specs.get("map_main")
    gdf_main = load_geodata(main_path, tolerance)
    main_geojson_str = gdf_main.to_json() if gdf_main is not None else None

    if main_geojson_str is None:
        st.info("⚠️ Camada principal não disponível. Exibindo mapa vazio.")
    else:
        size_mb = len(main_geojson_str.encode()) / 1_048_576
        if size_mb > _GEOJSON_WARN_MB:
            st.warning(
                f"⚠️ Camada principal ocupa **{size_mb:.0f} MB** após simplificação "
                f"(tolerância: `{tolerance}`). O mapa pode carregar lentamente ou ficar em branco. "
                f"Aumente `simplify_tolerance` em `specs.json` para reduzir o tamanho."
            )

    # ROI layer — fully optional, silent on failure
    roi_path = specs.get("map_roi")
    gdf_roi = load_geodata(roi_path, tolerance) if roi_path else None
    roi_geojson_str = gdf_roi.to_json() if gdf_roi is not None else None

    with st.spinner("🗺️ Carregando mapa..."):
        map_html = build_map(
            main_geojson_str=main_geojson_str,
            roi_geojson_str=roi_geojson_str,
            style_lookup_json=json.dumps(style_lookup),
            specs_json=json.dumps({
                "map_options": specs.get("map_options", {}),
                "columns": specs.get("columns", {}),
                "classes": specs.get("classes", ""),
                "style_roi": specs.get("style_roi", {}),
                "basemaps": specs.get("basemaps", []),
            }),
        )

    st.components.v1.html(map_html, width=None, height=700)


def render_statistics(specs: dict, style_lookup: dict):
    """Renders the statistics section: one horizontal bar chart tab per stats_charts entry."""
    st.markdown(f"## {specs['title_stats']}")
    desc = specs.get("desc_stats")
    if desc:
        st.markdown(desc)

    stats_path = specs.get("stats")
    df = load_statistics(stats_path)

    if df is None:
        st.warning("⚠️ Arquivo de estatísticas não encontrado ou inválido.")
        return

    classes_col = specs.get("stats_classes_column", specs.get("classes", ""))
    charts = specs.get("stats_charts", [])

    if not charts:
        st.info("ℹ️ Nenhum gráfico configurado em `stats_charts`.")
        return

    if classes_col not in df.columns:
        st.warning(f"⚠️ Coluna `{classes_col}` não encontrada no CSV de estatísticas.")
        return

    # Build name and color maps from style_lookup — unknown codes fall back to gray / code itself
    name_col = "__class_name__"
    code_to_name = {
        code: style_lookup.get(code, {}).get("name", code)
        for code in df[classes_col].astype(str).unique()
    }
    color_map = {
        code_to_name.get(code, code): style_lookup.get(code, {}).get("color", FALLBACK_COLOR)
        for code in df[classes_col].astype(str).unique()
    }

    tabs = st.tabs([chart["label"] for chart in charts])

    for tab, chart in zip(tabs, charts):
        col = chart.get("column")
        label = chart.get("label", col)
        x_label = chart.get("x_label", col)

        with tab:
            if col not in df.columns:
                st.warning(f"⚠️ Coluna `{col}` não encontrada no CSV.")
                continue

            df_chart = (
                df[[classes_col, col]]
                .dropna(subset=[col])
                .copy()
            )
            df_chart[classes_col] = df_chart[classes_col].astype(str)
            # Replace class code with full name for display
            df_chart[name_col] = df_chart[classes_col].map(code_to_name).fillna(df_chart[classes_col])
            df_chart = df_chart.sort_values(col, ascending=True)  # largest bar at top

            fig = px.bar(
                df_chart,
                x=col,
                y=name_col,
                orientation="h",
                color=name_col,
                color_discrete_map=color_map,
                title=label,
            )
            fig.update_layout(
                showlegend=False,
                xaxis_title=x_label,
                yaxis_title="",
                height=max(400, len(df_chart) * 30),  # scale height to number of classes
                margin=dict(l=10, r=10, t=40, b=10),
            )
            st.plotly_chart(fig, use_container_width=True)

# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    # load_specs must be called before set_page_config
    specs = load_specs(SPECS_PATH)

    st.set_page_config(
        page_title=specs["title_page"],
        page_icon=specs["page_icon"],
        layout="wide",
        initial_sidebar_state="expanded",
    )

    style_lookup = build_style_lookup(json.dumps(specs["style_classes"]))

    render_sidebar(specs)
    render_intro(specs)
    st.markdown("---")
    render_map(specs, style_lookup)
    st.markdown("---")
    render_statistics(specs, style_lookup)


if __name__ == "__main__":
    main()