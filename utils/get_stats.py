# -*- coding: utf-8 -*-
"""
compute_stats.py — Computes APCAC area statistics from a GeoPackage layer.

Usage:
    python compute_stats.py <gpkg_path> <layer_name> <cerrado_area_km2>

Example:
    python compute_stats.py data/apcac.gpkg apcac_nunivotto5 2038953.0

Output:
    stats.csv — one row per APCAC class with area and percentage columns.
"""

import sys
import geopandas as gpd
import pandas as pd


def compute_stats(gpkg_path: str, layer_name: str, cerrado_area_km2: float) -> pd.DataFrame:
    print(f"Reading layer '{layer_name}' from {gpkg_path}...")
    gdf = gpd.read_file(gpkg_path, layer=layer_name)

    print(f"  {len(gdf)} features loaded.")
    print(f"  Total Cerrado area provided: {cerrado_area_km2:,.2f} km²")

    # Group by class and sum catchment areas
    df = (
        gdf.groupby("cd_apcac")["nuareacont"]
        .sum()
        .reset_index()
        .rename(columns={"nuareacont": "area_km2"})
    )

    # Percentage of total Cerrado area
    df["area_km2_p"] = (df["area_km2"] / cerrado_area_km2 * 100).round(2)
    df["area_km2"] = df["area_km2"].round(2)

    # Sort by area descending
    df = df.sort_values("area_km2", ascending=False).reset_index(drop=True)

    return df


def main():
    if len(sys.argv) != 4:
        print(__doc__)
        sys.exit(1)

    gpkg_path = sys.argv[1]
    layer_name = sys.argv[2]

    try:
        cerrado_area_km2 = float(sys.argv[3])
    except ValueError:
        print(f"Error: cerrado_area_km2 must be a number, got '{sys.argv[3]}'")
        sys.exit(1)

    df = compute_stats(gpkg_path, layer_name, cerrado_area_km2)

    output_path = "stats.csv"
    df.to_csv(output_path, sep=";", index=False)

    print(f"\nResults:")
    print(df.to_string(index=False))
    print(f"\nSaved to {output_path}")
    print(f"Total classified area: {df['area_km2'].sum():,.2f} km²")
    print(f"Total classified area: {df['area_km2_p'].sum():.2f}% of Cerrado")


if __name__ == "__main__":
    main()