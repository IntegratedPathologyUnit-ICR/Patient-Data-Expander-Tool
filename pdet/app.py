#!/usr/bin/env python3
"""Patient Expander Tool - Standalone Panel App"""

import matplotlib
matplotlib.use('agg')

import warnings
warnings.filterwarnings("ignore")

import panel as pn
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import io
import json
import sys
import tempfile
import requests
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from gdc_index_code import build_gdc_index
import ehrapy as ep
import ehrdata as ed
import hvplot.pandas  # enables .hvplot accessor
import holoviews as hv
from rapidfuzz import process as rf_process, fuzz

from gdc_index_code import build_gdc_index


pn.extension("tabulator", "bokeh")

# ── Shared state (passed between tabs) ────────────────────────────────────────
state = {"df": None, "cleaned_df": None, "edata": None,
         "leiden_key": None, "mapping_results": None,
         "value_mappings": None, "cluster_profiles": None}

# ── Upload tab ────────────────────────────────────────────────────────────────
file_input       = pn.widgets.FileInput(accept=".csv,.tsv", name="")
status_md        = pn.pane.Markdown("", margin=(10, 0))
goto_analysis_btn = pn.widgets.Button(
    name="Proceed to Analysis →", button_type="success",
    width=220, visible=False, margin=(12, 0, 0, 0),
)
preview_tbl = pn.widgets.Tabulator(
    pd.DataFrame(), visible=False,
    pagination="local", page_size=10,
    sizing_mode="stretch_width",
)
meta_md = pn.pane.Markdown("", visible=False)

# ── Header-editing section ────────────────────────────────────────────────────
# Shown immediately after upload, before any analysis runs. Edits here rename
# the columns of `state["df"]` directly — every downstream step (cleaning,
# encoding, field matching) reads from `state["df"]`, so descriptive headers
# entered here flow through the whole pipeline without touching the user's file.
header_edit_msg = pn.pane.HTML(
    "<div style='background:#fff8e1;border:1px solid #ffd54f;border-radius:6px;"
    "padding:12px 16px;margin:12px 0'>"
    "<b>Please ensure variable headers are descriptive and written in full.</b><br>"
    "<span style='color:#555;font-size:13px'>Abbreviated or truncated headers will "
    "likely lead to incorrect matching with public database fields. You can edit "
    "these below (this will not edit your original file).</span>"
    "<hr style='border:none;border-top:1px solid #ffe7a3;margin:10px 0'>"
    "<b>Derived or recoded fields generally will not match public database fields.</b><br>"
    "<span style='color:#555;font-size:13px'>For example, a column "
    "<i>'History of colorectal cancer'</i> (values <i>Yes</i>/<i>No</i>) derived from a "
    "more general <i>'History of cancer: Type'</i> field (values e.g. <i>Colorectal "
    "Cancer</i>, <i>Lung Cancer</i>) will likely not be matched, since the public "
    "database stores the general field, not your derived one. Where possible, supply "
    "fields as close to their original, \"top-level\" form as you can.</span></div>",
    visible=False,
)
header_edit_container = pn.Column()
header_apply_btn = pn.widgets.Button(
    name="Apply header changes", button_type="primary", width=200, visible=False,
)
header_status_md = pn.pane.Markdown("")
_header_inputs: dict[str, pn.widgets.TextInput] = {}   # current_col → TextInput

def _build_header_editor(df):
    """(Re)build one editable row per column, pre-filled with its current name."""
    header_edit_container.clear()
    _header_inputs.clear()
    rows = []
    for col in df.columns:
        ti = pn.widgets.TextInput(value=str(col), width=320)
        _header_inputs[str(col)] = ti
        rows.append(pn.Row(
            pn.pane.HTML(
                f"<code style='font-size:12px;color:#888;width:220px;display:inline-block'>"
                f"{col}</code>",
            ),
            pn.pane.HTML("<span style='color:#aaa'>→</span>", width=20),
            ti,
        ))
    header_edit_container.objects = rows
    header_apply_btn.visible = True

def on_apply_headers(event):
    df = state.get("df")
    if df is None or not _header_inputs:
        return

    new_names = [ti.value.strip() or orig for orig, ti in _header_inputs.items()]
    if len(set(new_names)) != len(new_names):
        header_status_md.object = "❌ Header names must be unique — duplicate name found."
        return

    renamed = df.rename(columns=dict(zip(_header_inputs.keys(), new_names)))
    state["df"] = renamed

    n_changed = sum(1 for orig, new in zip(_header_inputs.keys(), new_names) if orig != new)
    header_status_md.object = f"**✓ Applied — {n_changed} header(s) renamed.**"

    meta_md.object = (
        f"**{len(renamed):,} rows &nbsp;·&nbsp; {len(renamed.columns)} columns**  \n"
        f"Columns: `{'`, `'.join(renamed.columns.tolist())}`"
    )
    preview_tbl.value = renamed
    _build_header_editor(renamed)   # rebuild so further edits stack on the new names

header_apply_btn.on_click(on_apply_headers)

def on_upload(event):
    if not event.new:
        return
    if file_input.value is None:
        return
    try:
        sep = "\t" if file_input.filename.endswith(".tsv") else ","
        df  = pd.read_csv(io.BytesIO(file_input.value), sep=sep)
        state["df"] = df

        status_md.object = f"✅ **{file_input.filename}** loaded successfully."
        meta_md.object   = (
            f"**{len(df):,} rows &nbsp;·&nbsp; {len(df.columns)} columns**  \n"
            f"Columns: `{'`, `'.join(df.columns.tolist())}`"
        )
        meta_md.visible          = True
        preview_tbl.value        = df
        preview_tbl.visible      = True
        goto_analysis_btn.visible = True

        header_edit_msg.visible = True
        header_status_md.object = ""
        _build_header_editor(df)

    except Exception as e:
        status_md.object = f"❌ Failed to read file: `{e}`"

file_input.param.watch(on_upload, "filename")

def on_goto_analysis(event):
    tabs.active = 1   # Analysis is tab index 1

goto_analysis_btn.on_click(on_goto_analysis)

upload_tab = pn.Column(
    pn.pane.HTML(
        "<h2>Upload Patient Data</h2>"
        "<p style='color:#555'>Upload your CSV or TSV file of patient data to begin. "
        "A preview will appear below once the file is loaded.</p>"
    ),
    pn.Column(
        pn.pane.HTML("<b>Select file</b>", margin=(0, 0, 4, 0)),
        file_input,
        styles={
            "background":    "#f8f9fb",
            "border":        "2px dashed #d0d4db",
            "border-radius": "8px",
            "padding":       "24px",
        },
        width=500,
    ),
    status_md,
    meta_md,
    header_edit_msg,
    pn.Column(header_edit_container, height=260, scroll=True),
    pn.Row(header_apply_btn, header_status_md, align="center"),
    goto_analysis_btn,
    pn.layout.Divider(visible=False),
    preview_tbl,
    width=980,
)

# ──────────────────────────────────────────────────────────────────────────────

# ── Generic data-cleaning helpers ─────────────────────────────────────────────

def _code_safe_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """Lowercase, spaces/specials → underscores, strip leading/trailing underscores."""
    safe = frame.copy()
    safe.columns = (
        safe.columns.astype(str)
        .str.strip()
        .str.lower()
        .str.replace(r"[^0-9a-zA-Z]+", "_", regex=True)
        .str.replace(r"_+", "_", regex=True)
        .str.strip("_")
    )
    return safe

def _missing_value_tokens() -> set[str]:
    return {"", "na", "n/a", "nan", "unknown", "fail / unknown",
            "equivocal / unknown", "equivicol / unknown"}

def _standardize_missing_values(frame: pd.DataFrame) -> pd.DataFrame:
    """Replace common missing-value strings with NaN across all columns."""
    cleaned = frame.copy()
    tokens  = _missing_value_tokens()
    for col in cleaned.columns:
        normalized = cleaned[col].map(
            lambda v: str(v).strip().lower() if pd.notna(v) else np.nan
        )
        cleaned.loc[normalized.isin(tokens), col] = np.nan
    return cleaned

# ── Cluster profiler (called after Leiden + Field Matching are complete) ───────

def profile_all_clusters(cleaned_df, edata, mapping_results, leiden_key):
    """
    For every Leiden cluster compute the dominant value and prevalence of each
    matched column. Returns a dict keyed by cluster ID string.
    Skips unmatched columns and high-cardinality continuous ones.
    """
    labelled = cleaned_df.copy()
    labelled["_cluster"] = edata.obs[leiden_key].values
    profiles = {}

    for cid in sorted(labelled["_cluster"].unique(), key=str):
        cdf      = labelled[labelled["_cluster"] == cid].drop(columns=["_cluster"])
        features = {}

        for col in cdf.columns:
            res = mapping_results.get(col)
            if not res:
                continue
            if res.get("status") == "unmatched" and res.get("query_type") != "genomic":
                continue
            s = cdf[col].dropna()
            if len(s) == 0:
                continue
            if pd.api.types.is_float_dtype(s) and s.nunique() > 10:
                continue
            vc = s.value_counts(normalize=True)
            features[col] = {
                "dominant_value": vc.index[0],
                "prevalence":     float(vc.iloc[0]),
                "value_counts":   vc.head(4).to_dict(),
                "gdc_field":      res.get("gdc_field"),
                "match_status":   res.get("status"),
                "query_type":     res.get("query_type", "clinical"),
                "gene_symbol":    res.get("gene_symbol"),
                "candidates":     res.get("candidates", []),
            }

        profiles[str(cid)] = {"n_patients": len(cdf), "features": features}

    print(f"{'Cluster':<10} {'Patients':<10} {'Matchable features'}")
    print("─" * 40)
    for cid, p in profiles.items():
        print(f"  {cid:<8} {p['n_patients']:<10} {len(p['features'])}")

    return profiles

# ──────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# Analysis tab — step-by-step wizard
# Steps reveal sequentially: Clean → Missingness → UMAP → Leiden
# ─────────────────────────────────────────────────────────────────────────────

def _step_header(n, title, subtitle=""):
    return pn.pane.HTML(
        f"<div style='display:flex;align-items:center;gap:12px;margin:16px 0 6px'>"
        f"<div style='background:#1a73e8;color:#fff;border-radius:50%;width:30px;height:30px;"
        f"display:flex;align-items:center;justify-content:center;font-weight:bold;flex-shrink:0'>{n}</div>"
        f"<div><b style='font-size:15px'>{title}</b>"
        + (f"<br><span style='color:#666;font-size:12px'>{subtitle}</span>" if subtitle else "")
        + "</div></div>"
    )

def _card(*contents, **kwargs):
    return pn.Column(
        *contents,
        styles={"border": "1px solid #e0e0e0", "border-radius": "8px",
                "padding": "16px", "background": "#fafafa", "margin": "8px 0"},
        **kwargs,
    )

# ── Step 1 widgets ────────────────────────────────────────────────────────────
step1_btn    = pn.widgets.Button(name="▶  Clean & Prepare", button_type="primary", width=180)
step1_status = pn.pane.Markdown("")
step1_out    = pn.Column(visible=False)

# ── Step 2 widgets ────────────────────────────────────────────────────────────
step2_card    = pn.Column(visible=False)   # reassigned after layout is built
miss_plt      = pn.pane.HoloViews(sizing_mode="stretch_width")
var_checkboxes_container = pn.Column()    # will hold dynamically created checkboxes
remove_btn    = pn.widgets.Button(name="Remove",                button_type="warning", width=160)
impute_btn    = pn.widgets.Button(name="Impute",                button_type="primary",  width=160)
continue_btn  = pn.widgets.Button(name="Continue to clustering", button_type="success", width=200)
step2_status  = pn.pane.Markdown("")

# ── Step 3 widgets ────────────────────────────────────────────────────────────
step3_card   = pn.Column(visible=False)
umap_plt     = pn.pane.HoloViews(sizing_mode="stretch_width")
step3_status = pn.pane.Markdown("")

# ── Step 4 widgets ────────────────────────────────────────────────────────────
step4_card   = pn.Column(visible=False)
res_slider   = pn.widgets.FloatSlider(
    name="Leiden Resolution  (↑ = more clusters)",
    start=0.1, end=2.0, step=0.1, value=0.3, width=420,
)
update_btn   = pn.widgets.Button(name="↺  Update clusters", button_type="warning", width=160)
leiden_plt            = pn.pane.HoloViews(sizing_mode="stretch_width")
compare_plt           = pn.pane.HoloViews(sizing_mode="stretch_width")
cluster_features_container = pn.Column()  # per-cluster defining-feature cards
step4_status = pn.pane.Markdown("")
proceed_btn  = pn.widgets.Button(
    name="Proceed to Field Matching →", button_type="success", width=240, visible=False,
)

# Analysis-local state (mirrors relevant keys into shared `state`)
astate = {"edata": None, "leiden_key": None, "cluster_df": None}

# ─────────────────────────────────────────────────────────────────────────────
# STEP 1 — Clean only; encoding happens after missingness decisions in Step 2
# ─────────────────────────────────────────────────────────────────────────────
def on_step1(event):
    if state.get("df") is None:
        step1_status.object = "❌ Upload a CSV on the Upload tab first."
        return
    step1_status.object = "*⏳ Cleaning data...*"
    try:
        df = state["df"].copy()
        df = _code_safe_columns(df)
        df = _standardize_missing_values(df)
        df = df.replace({pd.NA: np.nan})

        # Detect and store patient ID column — excluded from features and field matching
        id_col = next(
            (c for c in ["cprid", "patient_id", "patientid", "id", "subject_id"]
             if c in df.columns and df[c].is_unique),
            None,
        )
        state["id_col"] = id_col
        if id_col:
            df = df.drop(columns=[id_col])
        state["cleaned_df"] = df

        step1_out.objects = [
            pn.pane.HTML(
                f"<b>✓ {len(df.columns)} variables</b> &nbsp;·&nbsp; "
                f"<b>{len(df):,} patients</b>"
                + (f"  &nbsp;·&nbsp; <i>index: {id_col}</i>" if id_col else "")
            )
        ]
        step1_out.visible = True
        step1_status.object = "**✓ Done — review missingness below.**"
        _build_missingness_step(df)
        step2_card.visible = True

    except Exception as e:
        import traceback
        step1_status.object = f"❌ Step 1 failed: `{e}`"
        traceback.print_exc()

step1_btn.on_click(on_step1)

# ─────────────────────────────────────────────────────────────────────────────
# STEP 2 — Missingness inspection & decision (on original columns, before encoding)
# ─────────────────────────────────────────────────────────────────────────────

def _create_anndata(df):
    """One-hot encode a cleaned DataFrame and return an AnnData ready for clustering.
    Called after the user has handled missingness on the original columns.
    The ID column must already be dropped from df before calling this."""
    adata = ed.io.from_pandas(df)
    adata.layers["raw_data"] = adata.X.copy()

    x_df = pd.DataFrame(adata.X, columns=adata.var_names, index=adata.obs_names)
    object_cols = x_df.select_dtypes(include=['object']).columns.tolist()

    if object_cols:
        for col in object_cols:
            try:
                x_df[col] = pd.to_numeric(x_df[col], errors='raise')
            except (ValueError, TypeError):
                pass

        remaining_object = x_df.select_dtypes(include=['object']).columns.tolist()
        if remaining_object:
            # Preserve NaN through get_dummies (default fills NaN rows with 0)
            nan_masks = {col: x_df[col].isna() for col in remaining_object}
            x_df = pd.get_dummies(x_df, columns=remaining_object, drop_first=False, dtype=float)
            for orig_col, mask in nan_masks.items():
                if mask.any():
                    dummy_cols = [c for c in x_df.columns if c.startswith(orig_col + "_")]
                    x_df.loc[mask, dummy_cols] = np.nan

        adata = ed.io.from_pandas(x_df, index_column=None)
        adata.layers["raw_data"] = adata.X.copy()

    # Drop reference-level dummies (_no / _false) and missing-indicator dummies (_nan etc.)
    baseline_suffixes = ["_nan", "_no", "_false", "_missing", "_unknown"]
    cols_to_drop = [v for v in adata.var_names
                   if any(v.lower().endswith(s) for s in baseline_suffixes)]
    if cols_to_drop:
        adata = adata[:, [v for v in adata.var_names if v not in cols_to_drop]].copy()

    # Ensure adata.X is a clean float array
    _x_san = pd.DataFrame(
        np.asarray(adata.X, dtype=object), columns=adata.var_names
    ).apply(pd.to_numeric, errors="coerce")
    adata.X = _x_san.values.astype(float)
    return adata


def _build_missingness_step(df):
    """Render missingness chart and checkboxes for the original (pre-encoding) columns."""
    missing_pct_all = df.isna().mean().sort_values(ascending=False) * 100
    missing_pct = missing_pct_all[missing_pct_all > 0]

    if missing_pct.empty:
        empty_df = pd.DataFrame({'field': ['(all complete)'], 'missing_pct': [0.0]})
        miss_plt.object = empty_df.hvplot.bar(
            x='field', y='missing_pct',
            title='✓ No missing values detected',
            height=150, width=350, legend=False,
            color='#2e7d32',
        )
    else:
        missing_df = missing_pct.reset_index()
        missing_df.columns = ['field', 'missing_pct']
        plot = missing_df.hvplot.bar(
            x='field', y='missing_pct',
            title='Missingness by field (red >30%, amber >10%, blue ≤10%)',
            ylabel='Missing (%)',
            height=350, width=max(600, len(missing_pct) * 40),
            color=missing_df['missing_pct'].map(
                lambda v: "#e53935" if v > 30 else "#fb8c00" if v > 10 else "#4C78A8"
            ),
            legend=False
        )
        plot = plot * hv.HLine(30).opts(color="#e53935", line_dash="dashed") \
                     * hv.HLine(10).opts(color="#fb8c00", line_dash="dashed")
        miss_plt.object = plot

    var_checkboxes_container.clear()
    rows = []
    for var in missing_pct.index:
        miss_pct = missing_pct[var]
        cb = pn.widgets.Checkbox(value=False, name="")
        label = pn.pane.HTML(
            f"<span style='width:400px;display:inline-block'>"
            f"<code>{var}</code> &nbsp; "
            f"<span style='color:#{'e53935' if miss_pct > 30 else 'fb8c00' if miss_pct > 10 else '999'};font-weight:bold'>"
            f"({miss_pct:.1f}%)</span></span>"
        )
        row = pn.Row(cb, label, width=600)
        rows.append((var, cb, row))

    for var, cb, row in rows:
        var_checkboxes_container.append(row)

    _build_missingness_step._checkboxes = {var: cb for var, cb, _ in rows}


def _invalidate_clustering():
    """Hide and clear steps 3/4 so stale cluster results are never shown after field changes."""
    step3_card.visible = False
    step4_card.visible = False
    astate["edata"] = None
    astate["leiden_key"] = None
    astate["cluster_df"] = None
    proceed_btn.visible = False
    cluster_features_container.clear()
    umap_plt.object = None
    leiden_plt.object = None
    compare_plt.object = None


def on_remove(event):
    """Drop selected original columns from cleaned_df and refresh the missingness view."""
    df = state.get("cleaned_df")
    if df is None:
        return
    checkboxes = getattr(_build_missingness_step, '_checkboxes', {})
    to_drop = [col for col, cb in checkboxes.items() if cb.value]

    if not to_drop:
        step2_status.object = "⚠️ No fields selected to remove."
        return

    step2_status.object = f"*⏳ Removing {len(to_drop)} fields...*"
    df = df.drop(columns=to_drop)
    state["cleaned_df"] = df
    _invalidate_clustering()
    step2_status.object = f"**✓ Removed {len(to_drop)} fields.** Adjust remaining fields or proceed."
    _build_missingness_step(df)
    for cb in getattr(_build_missingness_step, '_checkboxes', {}).values():
        cb.value = False


def on_impute(event):
    """Impute selected original columns with median/mode and refresh the missingness view."""
    df = state.get("cleaned_df")
    if df is None:
        return
    checkboxes = getattr(_build_missingness_step, '_checkboxes', {})
    to_impute = [col for col, cb in checkboxes.items() if cb.value]

    if not to_impute:
        step2_status.object = "⚠️ No fields selected for imputation."
        return

    step2_status.object = f"*⏳ Imputing {len(to_impute)} selected fields...*"
    df = df.copy()
    imputed = 0
    for col in to_impute:
        if col in df.columns and df[col].isna().any():
            if pd.api.types.is_numeric_dtype(df[col]):
                df[col] = df[col].fillna(df[col].median())
            else:
                df[col] = df[col].fillna(df[col].mode()[0])
            imputed += 1
    state["cleaned_df"] = df
    _invalidate_clustering()
    step2_status.object = f"**✓ Imputed {imputed} fields.** Adjust remaining fields or proceed."
    _build_missingness_step(df)
    for cb in getattr(_build_missingness_step, '_checkboxes', {}).values():
        cb.value = False


def on_continue(event):
    """Encode the cleaned df then proceed to UMAP."""
    df = state.get("cleaned_df")
    if df is None:
        step2_status.object = "❌ No data available — run Step 1 first."
        return

    # Block if any missing values remain — user must remove or impute all of them.
    n_missing_cols = df.isna().any().sum()
    if n_missing_cols > 0:
        missing_names = ", ".join(df.columns[df.isna().any()].tolist())
        step2_status.object = (
            f"❌ **{n_missing_cols} column(s) still have missing values:** {missing_names}. "
            "Please remove or impute them before continuing."
        )
        return

    step2_status.object = "*⏳ Encoding categorical variables for clustering...*"
    try:
        adata = _create_anndata(df)
        astate["edata"] = adata
        state["edata"]  = adata
        astate["cluster_df"] = df.copy()
        step2_status.object = "*⏳ Proceeding to UMAP...*"
        _run_step3(adata, df)
    except Exception as e:
        import traceback; traceback.print_exc()
        step2_status.object = f"❌ Encoding failed: `{e}`"

remove_btn.on_click(on_remove)
impute_btn.on_click(on_impute)
continue_btn.on_click(on_continue)

# ─────────────────────────────────────────────────────────────────────────────
# STEP 3 — Normalise → neighbours → UMAP
# ─────────────────────────────────────────────────────────────────────────────
def _run_step3(adata, cleaned_df):
    from sklearn.preprocessing import MinMaxScaler

    step3_card.visible  = True
    step3_status.object = "*⏳ Normalising (min-max)...*"
    try:
        x_np = np.asarray(adata.X, dtype=float)

        if np.isnan(x_np).any():
            # This should not happen if on_continue blocked incomplete data,
            # but NaN can appear in one-hot dummies (NaN-preserved from get_dummies).
            # Only impute the dummy columns — the user already handled original columns.
            col_medians = np.nanmedian(x_np, axis=0)
            nan_idx = np.where(np.isnan(x_np))
            x_np[nan_idx] = col_medians[nan_idx[1]]

        adata.X = MinMaxScaler().fit_transform(x_np)
        astate["edata"] = adata
        state["edata"]  = adata

        # Apply PCA to balance variable weighting (especially high-cardinality vars like location)
        step3_status.object = "*⏳ Applying PCA for balanced feature weighting...*"
        n_vars = adata.n_vars
        n_comps = min(20, max(5, n_vars // 2))
        pca_done = False
        try:
            ep.pp.pca(adata, n_comps=n_comps)
            step3_status.object = f"*⏳ PCA reduced {n_vars} features to {n_comps} components...*"
            pca_done = True
        except Exception as e:
            step3_status.object = f"*⚠️ PCA failed, proceeding without dimensionality reduction: {e}*"
            print(f"PCA error: {e}")

        step3_status.object = "*⏳ Building neighbour graph...*"
        try:
            ep.pp.neighbors(adata, use_rep='X_pca' if pca_done else 'X')
        except Exception as e:
            step3_status.object = f"❌ Neighbors failed: {e}"
            print(f"Neighbors error: {e}")
            import traceback
            traceback.print_exc()
            raise

        step3_status.object = "*⏳ Computing UMAP (may take ~1 min)...*"
        ep.tl.umap(adata)

        # Plot UMAP using hvplot
        umap_df = pd.DataFrame(
            adata.obsm['X_umap'],
            columns=['UMAP1', 'UMAP2'],
            index=adata.obs_names
        )
        plot = umap_df.hvplot.scatter(x='UMAP1', y='UMAP2',
                                       title='UMAP Embedding',
                                       height=400, width=600,
                                       s=50, alpha=0.6, color='#1f77b4')
        umap_plt.object = plot

        step3_status.object = "**✓ UMAP ready — adjust clusters below.**"
        step4_card.visible  = True
        _run_leiden(adata, cleaned_df, res_slider.value)

    except Exception as e:
        import traceback
        step3_status.object = f"❌ Step 3 failed: `{e}`"
        traceback.print_exc()

# ─────────────────────────────────────────────────────────────────────────────
# STEP 4 helpers
# ─────────────────────────────────────────────────────────────────────────────
def _build_cluster_comparison(adata, leiden_key, cleaned_df):
    """
    Build per-cluster defining-feature cards from cleaned_df (original column names).
    cleaned_df is passed explicitly (not read from state) to guarantee it matches
    the adata that was used for clustering.
    Deviation data is stored in astate for the checkbox-driven comparison chart,
    which renders below the cards so ticking a variable doesn't snap the viewport.
    """
    cluster_features_container.clear()
    _build_cluster_comparison._cluster_checkboxes = {}
    compare_plt.object = None  # blank until user ticks features

    if cleaned_df is None:
        return

    df_c     = cleaned_df.copy()
    df_c["_cluster"] = adata.obs[leiden_key].values
    clusters = sorted(df_c["_cluster"].unique(), key=str)

    # Pre-compute deviation for ALL columns × ALL clusters and cache for the chart.
    # deviation is normalised 0-1 so numeric and categorical variables share one axis.
    dev_records: list[dict] = []

    def _make_enforcer(cbs, counter_md):
        def _enforce(event):
            n = sum(cb.value for cb in cbs)
            counter_md.object = (
                f"**{n} / 5 selected**"
                if n <= 5
                else f"**<span style='color:#e53935'>{n} / 5 — max 5</span>**"
            )
            for cb in cbs:
                cb.disabled = (not cb.value) and (n >= 5)
        return _enforce

    for c_id in clusters:
        c_str      = str(c_id)
        cluster_df = df_c[df_c["_cluster"] == c_id]
        n_patients = len(cluster_df)

        features = []
        for col in cleaned_df.columns:
            col_all = df_c[col].dropna()
            col_c   = cluster_df[col].dropna()
            if col_all.empty or col_c.empty:
                continue

            if pd.api.types.is_numeric_dtype(col_all):
                overall_val = col_all.mean()
                cluster_val = col_c.mean()
                col_range   = col_all.max() - col_all.min()
                deviation   = abs(cluster_val - overall_val) / (col_range if col_range > 0 else 1)
                features.append({
                    "variable":  col, "value": f"{cluster_val:.2f}",
                    "c_label":   f"{cluster_val:.2f}", "o_label": f"{overall_val:.2f}",
                    "deviation": deviation,
                })
            else:
                dominant = col_c.mode()[0]
                c_prev   = (col_c == dominant).mean()
                o_prev   = (col_all == dominant).mean()
                deviation = abs(c_prev - o_prev)
                features.append({
                    "variable":  col, "value": str(dominant),
                    "c_label":   f"{c_prev*100:.0f}%", "o_label": f"{o_prev*100:.0f}%",
                    "deviation": deviation,
                })
            dev_records.append({"variable": col, "cluster": f"C{c_str}", "deviation": deviation})

        features.sort(key=lambda x: x["deviation"], reverse=True)
        top_feats = features[:20]

        cbs        = []
        counter_md = pn.pane.Markdown("0 / 5 selected", width=130)
        rows       = []

        for feat in top_feats:
            cb = pn.widgets.Checkbox(value=False, name="")
            label = pn.pane.HTML(
                f"<span style='width:520px;display:inline-block'>"
                f"<code>{feat['variable']}</code> = <b>{feat['value']}</b>"
                f"&nbsp;<span style='color:#1a73e8'>{feat['c_label']}</span>"
                f"<span style='color:#aaa;font-size:11px'>"
                f" (overall {feat['o_label']})</span></span>"
            )
            cbs.append(cb)
            rows.append(pn.Row(cb, label, width=680))

        enforcer = _make_enforcer(cbs, counter_md)
        for cb in cbs:
            cb.param.watch(enforcer, "value")
            cb.param.watch(lambda ev: _refresh_compare_plot(), "value")

        card = pn.Column(
            pn.Row(
                pn.pane.HTML(
                    f"<b style='font-size:14px'>Cluster {c_id}</b>"
                    f"<span style='color:#666;font-size:12px;margin-left:8px'>"
                    f"{n_patients} patients</span>"
                ),
                counter_md,
                align="center",
            ),
            pn.Column(*rows, height=210, scroll=True),
            styles={
                "border": "1px solid #d0d4db", "border-radius": "6px",
                "padding": "12px 14px", "margin": "6px 0", "background": "#fff",
            },
        )
        cluster_features_container.append(card)
        _build_cluster_comparison._cluster_checkboxes[c_str] = [
            (feat["variable"], feat["value"], cb)
            for feat, cb in zip(top_feats, cbs)
        ]

    # Store deviation data for the chart — keyed by original column names so
    # checkbox lookups match exactly.
    astate["compare_deviation_df"] = pd.DataFrame(dev_records)


def _refresh_compare_plot():
    """Rebuild the cluster-comparison chart from whichever variables are currently
    ticked. Uses pre-computed deviation scores (0-1) from cleaned_df so variable
    names match the checkboxes exactly. Clears the chart when nothing is ticked."""
    dev_df = astate.get("compare_deviation_df")
    if dev_df is None or dev_df.empty:
        return

    checked_vars: set[str] = set()
    for feats in getattr(_build_cluster_comparison, "_cluster_checkboxes", {}).values():
        for var, _val, cb in feats:
            if cb.value:
                checked_vars.add(var)

    if not checked_vars:
        compare_plt.object = None
        return

    # Order variables by their max deviation across clusters (most changed first).
    max_dev = (
        dev_df[dev_df["variable"].isin(checked_vars)]
        .groupby("variable")["deviation"].max()
        .sort_values(ascending=False)
    )
    ordered   = max_dev.index.tolist()
    filtered  = dev_df[dev_df["variable"].isin(ordered)].copy()
    filtered["variable"] = pd.Categorical(filtered["variable"], categories=ordered, ordered=True)
    filtered  = filtered.sort_values("variable")
    n_clusters = filtered["cluster"].nunique()

    compare_plt.object = filtered.hvplot.bar(
        x="variable", y="deviation", by="cluster",
        title="Deviation from overall cohort for selected features (left = most changed)",
        ylabel="Deviation from overall (0 = same, 1 = max difference)",
        xlabel="",
        height=380,
        width=max(560, len(ordered) * (n_clusters + 1) * 20),
        rot=45, legend="right", stacked=False,
    )

# ─────────────────────────────────────────────────────────────────────────────
# STEP 4 — Leiden clustering + cluster comparison
# ─────────────────────────────────────────────────────────────────────────────
def _run_leiden(adata, cleaned_df, resolution):
    key = f"leiden_r{resolution:.1f}".replace(".", "_")
    step4_status.object = f"*⏳ Running Leiden (resolution={resolution:.1f})...*"
    if key not in adata.obs.columns:
        ep.tl.leiden(adata, resolution=resolution, key_added=key)
    astate["leiden_key"] = key
    state["leiden_key"]  = key

    n = adata.obs[key].nunique()

    # Plot Leiden-colored UMAP using hvplot
    umap_df = pd.DataFrame(
        adata.obsm['X_umap'],
        columns=['UMAP1', 'UMAP2'],
        index=adata.obs_names
    )
    umap_df['Cluster'] = adata.obs[key].values.astype(str)

    plot_leiden = umap_df.hvplot.scatter(
        x='UMAP1', y='UMAP2', c='Cluster',
        title=f'Leiden clusters (r={resolution:.1f}, n={n})',
        cmap='Category20', height=400, width=600,
        s=50, alpha=0.7, legend='right'
    )
    leiden_plt.object = plot_leiden

    step4_status.object = "*⏳ Building cluster comparison...*"
    _build_cluster_comparison(adata, key, cleaned_df)

    step4_status.object = (
        f"**✓ {n} clusters at resolution {resolution:.1f}.** "
        "Adjust the slider and click Update, or proceed when satisfied."
    )
    proceed_btn.visible = True

def on_update_leiden(event):
    if astate.get("edata") is not None and astate.get("cluster_df") is not None:
        _run_leiden(astate["edata"], astate["cluster_df"], res_slider.value)

update_btn.on_click(on_update_leiden)

def on_proceed(event):
    cluster_cbs = getattr(_build_cluster_comparison, "_cluster_checkboxes", {})
    if not cluster_cbs:
        step4_status.object = "⚠️ No cluster data — run clustering first."
        return

    defining = {}
    for c_id, feat_list in cluster_cbs.items():
        selected = [(var, val) for var, val, cb in feat_list if cb.value]
        if len(selected) > 5:
            step4_status.object = (
                f"⚠️ Cluster {c_id} has {len(selected)} features selected — max 5."
            )
            return
        defining[c_id] = selected

    if not any(defining.values()):
        step4_status.object = "⚠️ Select at least one defining feature for at least one cluster."
        return

    state["cluster_defining_features"] = defining
    n_total = sum(len(v) for v in defining.values())
    step4_status.object = (
        f"**✓ {n_total} features across {len(defining)} clusters saved — "
        f"switching to Field Matching.**"
    )
    tabs.active = 2

proceed_btn.on_click(on_proceed)

# ─────────────────────────────────────────────────────────────────────────────
# Assemble the tab
# ─────────────────────────────────────────────────────────────────────────────
analysis_tab = pn.Column(
    pn.pane.HTML(
        "<h2>Cluster Analysis</h2>"
        "<p style='color:#555'>Step-by-step preprocessing, UMAP clustering, and archetype profiling. "
    ),
    _card(
        _step_header(1, "Clean & Prepare",
                     "Standardise headers, encode categoricals, infer feature types"),
        step1_btn, step1_status, step1_out,
    ),
    _card(
        _step_header(2, "Review Missingness",
                     "Select fields to drop or impute. Then continue to clustering"),
        miss_plt,
        pn.layout.Spacer(height=12),
        pn.pane.HTML("<b>Click to select fields for removal or imputation:</b>"),
        pn.layout.Divider(margin=(8, 0)),
        pn.Column(var_checkboxes_container, height=200, scroll=True),
        pn.Row(remove_btn, impute_btn, continue_btn),
        step2_status,
        visible=False,
    ),
    _card(
        _step_header(3, "Normalise & UMAP",
                     "Min-max normalisation → neighbour graph → UMAP embedding"),
        step3_status, umap_plt,
        visible=False,
    ),
    _card(
        _step_header(4, "Leiden Clustering",
                     "Adjust resolution until the cluster structure looks meaningful"),
        pn.Row(res_slider, update_btn, align="end"),
        step4_status,
        leiden_plt,
        pn.layout.Divider(),
        _step_header("", "Define cluster signatures",
                     "For each cluster tick up to 5 variables that best represent it — "
                     "these will be used in Field Matching"),
        pn.layout.Divider(margin=(8, 0)),
        cluster_features_container,
        pn.layout.Spacer(height=8),
        proceed_btn,
        pn.layout.Spacer(height=16),
        _step_header("", "Cluster comparison",
                     "Deviation from overall cohort for your selected features — updates as you tick"),
        compare_plt,
        visible=False,
    ),
    width=1060,
)

# Wire step card references to the assembled layout so visibility toggles work
step2_card = analysis_tab[2]
step3_card = analysis_tab[3]
step4_card = analysis_tab[4]

# ──────────────────────────────────────────────────────────────────────────────

# ══════════════════════════════════════════════════════════════════════════════
# FIELD MATCHING  — backend helpers
# ══════════════════════════════════════════════════════════════════════════════

_CACHE_DIR     = Path(tempfile.gettempdir()) / "pdet_gdc_cache"
_CLONE_DIR     = _CACHE_DIR / "gdcdictionary"
_SCHEMAS_DIR   = _CLONE_DIR / "src/gdcdictionary/schemas"
_DATA_DIR      = Path(__file__).parent / "data"
_INDEX_PATH    = _DATA_DIR / "gdc_field_index.json"
_EMB_PATH      = _DATA_DIR / "gdc_field_embeddings.npy"
_EMB_KEYS_PATH = _DATA_DIR / "gdc_field_embedding_keys.json"

# Lazy-loaded singletons — populated on first call to _ensure_model()
_gdc_index        = None
_clinical_index   = None
_gdc_keys         = None
_embeddings       = None
_model            = None
_gdc_corpus_dict  = None
_sci_nlp          = None   # scispacy en_core_sci_sm (optional, graceful fallback)

CLINICAL_ENTITIES = {
    "demographic", "diagnosis", "exposure", "family_history",
    "follow_up", "treatment", "case", "molecular_test", "pathology_detail",
}
ENTITY_TO_API_PREFIX = {
    "diagnosis":        "diagnoses",
    "demographic":      "demographic",
    "follow_up":        "follow_ups",
    "treatment":        "treatments",
    "exposure":         "exposures",
    "family_history":   "family_histories",
    "pathology_detail": "diagnoses.pathology_details",
    "molecular_test":   "follow_ups.molecular_tests",
    "case":             "cases",
}
EXACT_THRESHOLD  = 1.00
AUTO_THRESHOLD   = 0.88
REVIEW_THRESHOLD = 0.65

ABBREV_TABLE = {
    "lvi": "lymphatic invasion present", "emvi": "vascular invasion present",
    "lvi_present": "lymphatic invasion present", "emvi_present": "vascular invasion present",
    "msi": "microsatellite instability", "msi_h": "microsatellite instability high",
    "msi_l": "microsatellite instability low", "mss": "microsatellite stable",
    "msi_status": "microsatellite instability status",
    "dob": "date of birth", "age": "age at diagnosis",
    "sex": "sex at birth", "gender": "sex at birth", "bmi": "body mass index",
    "stage": "ajcc clinical stage", "ajcc_stage": "ajcc clinical stage",
    "pt": "ajcc pathologic t", "pn": "ajcc pathologic n", "pm": "ajcc pathologic m",
    "t_stage": "ajcc pathologic t", "n_stage": "ajcc pathologic n", "m_stage": "ajcc pathologic m",
    "grade": "tumor grade", "diff": "tumor grade", "differentiation": "tumor grade",
    "os": "days to death", "os_months": "days to death",
    "dfs": "days to recurrence", "rfs": "days to recurrence",
    "vital_status": "vital status", "death": "vital status",
    "ecog": "ecog performance status", "ps": "performance status",
    "histo": "primary diagnosis", "histology": "primary diagnosis",
    "site": "primary site", "primary_site": "primary site", "location": "primary site",
    "kras": "KRAS proto-oncogene mutation", "braf": "BRAF proto-oncogene mutation",
    "nras": "NRAS oncogene mutation", "tp53": "TP53 tumor protein p53 mutation",
    "sexcat": "sex at birth", "ageatcrc": "age at diagnosis",
    "deathcat": "vital status", "crcdeath": "vital status cause of death",
    "msistatus": "microsatellite instability",
    "family_hx_crc": "family history cancer relative",
    "personal_hx_cancer": "prior malignancy",
    "gradedifferentiation": "tumor grade",
}

GENE_ENSEMBL_IDS = {
    "KRAS": "ENSG00000133703", "BRAF": "ENSG00000157764",
    "NRAS": "ENSG00000213281", "HRAS": "ENSG00000174775",
    "PIK3CA": "ENSG00000121879", "APC": "ENSG00000134982",
    "TP53": "ENSG00000141510", "SMAD4": "ENSG00000141646",
    "MLH1": "ENSG00000076242", "MSH2": "ENSG00000095002",
    "MSH6": "ENSG00000116062", "PMS2": "ENSG00000123191",
    "ERBB2": "ENSG00000141736", "EGFR": "ENSG00000146648",
    "PTEN": "ENSG00000171862", "CTNNB1": "ENSG00000168036",
}
_GENE_SUFFIXES = ("_status", "_mut", "_mutation", "_variant", "_snp", "_alteration")

def _ensure_model(status_cb=None):
    """Lazily load GDC index, embeddings, sentence model, and scispacy NER."""

    global _gdc_index, _clinical_index, _gdc_keys, _embeddings, _model, _gdc_corpus_dict, _sci_nlp
    if _model is not None:
        return True
    try:
        if not _INDEX_PATH.exists():
            if status_cb:
                status_cb("*⏳ Building GDC field index from repository (first run — ~30 s)…*")
            build_gdc_index(clone_dir=_CLONE_DIR, schemas_dir=_SCHEMAS_DIR, output_path=_INDEX_PATH)
        if status_cb:
            status_cb("*⏳ Loading GDC field index…*")
        with open(_INDEX_PATH) as f:
            _gdc_index = json.load(f)
        _clinical_index = {k: v for k, v in _gdc_index.items()
                           if v["entity"] in CLINICAL_ENTITIES}
        _gdc_corpus_dict = {
            k: f"{v['field'].replace('_',' ')} {v.get('description','')}".strip()
            for k, v in _clinical_index.items()
        }
        if status_cb:
            status_cb("*⏳ Loading sentence model (first run may take ~30 s)…*")
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer("pritamdeka/S-PubMedBert-MS-MARCO")
        if _EMB_PATH.exists() and _EMB_KEYS_PATH.exists():
            if status_cb:
                status_cb("*⏳ Loading cached field embeddings…*")
            _embeddings = np.load(_EMB_PATH)
            with open(_EMB_KEYS_PATH) as f:
                _gdc_keys = json.load(f)
        else:
            if status_cb:
                status_cb("*⏳ Building field embeddings (first run, ~1 min)…*")
            corpus = [_gdc_corpus_dict[k] for k in _clinical_index]
            _gdc_keys = list(_clinical_index.keys())
            _embeddings = _model.encode(corpus, show_progress_bar=False, convert_to_numpy=True)
            np.save(_EMB_PATH, _embeddings)
            with open(_EMB_KEYS_PATH, "w") as f:
                json.dump(_gdc_keys, f)

        # Scispacy is optional — silently skip if unavailable or OOM
        if status_cb:
            status_cb("*⏳ Loading scispacy biomedical NER model…*")
        try:
            import spacy, warnings as _w
            _w.filterwarnings("ignore")
            _sci_nlp = spacy.load("en_core_sci_sm")
        except Exception:
            pass  # scispacy unavailable; pipeline continues without it

        return True
    except Exception as e:
        if status_cb:
            status_cb(f"❌ Model load failed: `{e}`")
        return False


import re as _re

def _scispacy_queries(col):
    """
    Generate additional query strings from a column name using scispacy NER/
    lemmatisation and wordninja compound-word splitting.

    Strategy:
    1. Underscore → space (always emitted if different from col).
    2. Per-token wordninja split: only for tokens that are ≥8 chars and
       all-alpha (avoids mangling short acronyms like 'lvi', 'ecog').
    3. scispacy biomedical NER — each detected entity span as its own query.
    4. scispacy lemmatised non-stop tokens.

    Returns a deduplicated list ordered from most to least derived.
    Returns [] if scispacy is not loaded.
    """
    if _sci_nlp is None:
        return []

    import warnings as _w
    _w.filterwarnings("ignore")

    queries: list[str] = []
    seen: set[str] = {col.lower()}

    def _add(q: str):
        q = q.lower().strip()
        if q and q not in seen and len(q) > 2:
            queries.append(q)
            seen.add(q)

    # 1. CamelCase split + underscore → space
    text = _re.sub(r"([a-z])([A-Z])", r"\1 \2", col).replace("_", " ").lower().strip()
    _add(text)

    # 2. Wordninja per-token split (only for long, all-alpha tokens)
    wn_parts: list[str] = []
    try:
        import wordninja as _wn
        for tok in col.split("_"):
            if len(tok) >= 8 and tok.isalpha():
                wn_parts.extend(_wn.split(tok.lower()))
            else:
                wn_parts.append(tok.lower())
        _add(" ".join(wn_parts))
    except ImportError:
        pass

    # 3. scispacy NER on the space-separated text
    doc = _sci_nlp(text)
    for ent in doc.ents:
        _add(ent.text.lower())

    # 4. Lemmatised non-stop tokens
    lemma_parts = [
        t.lemma_.lower() for t in doc
        if not t.is_stop and not t.is_punct and t.lemma_.strip()
    ]
    _add(" ".join(lemma_parts))

    return queries


def _stage3_semantic_multi(queries: list[str], top_n: int = 10) -> list[dict]:
    """
    Semantic search across multiple query strings in one forward pass.
    For each corpus field we take the maximum similarity across all queries,
    then return the top_n fields.
    """
    if not queries or _model is None or _embeddings is None:
        return []
    q_embs = _model.encode(queries, convert_to_numpy=True)
    # shape: (n_queries, n_corpus)
    sim_matrix = np.array([[_cosine(qe, e) for e in _embeddings] for qe in q_embs])
    best_sims = sim_matrix.max(axis=0)
    idx = np.argsort(best_sims)[::-1][:top_n]
    return [{"gdc_field": _to_api_path(_gdc_keys[i]), "index_path": _gdc_keys[i],
             "score": float(best_sims[i]), "stage": "semantic"}
            for i in idx]

def _norm(col):
    return col.lower().replace("-", "_").replace(" ", "_").strip()

def _to_api_path(index_path):
    entity, _, field = index_path.partition(".")
    prefix = ENTITY_TO_API_PREFIX.get(entity, entity)
    return f"{prefix}.{field}" if field else prefix

def _expand(col):
    return ABBREV_TABLE.get(_norm(col), col)

def _detect_gene(col):
    norm = _norm(col).upper()
    for suf in _GENE_SUFFIXES:
        if norm.endswith(suf.upper()):
            cand = norm[: -len(suf)]
            if cand in GENE_ENSEMBL_IDS:
                return {"gene_symbol": cand, "ensembl_id": GENE_ENSEMBL_IDS[cand]}
    if norm in GENE_ENSEMBL_IDS:
        return {"gene_symbol": norm, "ensembl_id": GENE_ENSEMBL_IDS[norm]}
    return None

def _cosine(a, b):
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))

def _stage1_exact(col):
    norm = _norm(col)
    for ip, meta in _clinical_index.items():
        if norm == meta["field"] or norm == ip:
            return {"gdc_field": _to_api_path(ip), "index_path": ip,
                    "score": 1.0, "stage": "exact"}
    return None

def _stage2_fuzzy(col, top_n=10):
    norm = _norm(col)
    if len(norm) <= 5:
        return []
    results = rf_process.extract(norm, _gdc_corpus_dict, scorer=fuzz.WRatio, limit=top_n)
    return [{"gdc_field": _to_api_path(ip), "index_path": ip,
             "score": sc / 100, "stage": "fuzzy"}
            for _, sc, ip in results]

def _stage3_semantic(col, top_n=5):
    qemb = _model.encode([col], convert_to_numpy=True)[0]
    sims = [_cosine(qemb, e) for e in _embeddings]
    idx  = np.argsort(sims)[::-1][:top_n]
    return [{"gdc_field": _to_api_path(_gdc_keys[i]), "index_path": _gdc_keys[i],
             "score": sims[i], "stage": "semantic"}
            for i in idx]

def _match_column(col):
    gene = _detect_gene(col)
    if gene:
        return {"gdc_field": None, "index_path": None, "score": 1.0, "stage": "genomic",
                "status": "auto", "column": col, "expanded": col, "query_type": "genomic",
                "gene_symbol": gene["gene_symbol"], "ensembl_id": gene["ensembl_id"],
                "candidates": []}

    expanded    = _expand(col)
    abbrev_used = expanded != col
    sci_queries = _scispacy_queries(col)

    # Stage 1: exact match — try column, abbreviation expansion, and scispacy variants
    exact_candidates = [col, expanded] if abbrev_used else [col]
    for sq in sci_queries:
        if sq not in exact_candidates:
            exact_candidates.append(sq)
    for q in exact_candidates:
        r = _stage1_exact(q)
        if r:
            return {**r, "status": "auto", "column": col, "expanded": expanded,
                    "sci_expanded": sci_queries[0] if sci_queries else None,
                    "query_type": "clinical", "candidates": []}

    # Stage 2: fuzzy — run on primary term + distinct scispacy queries
    primary_term  = expanded if abbrev_used else col
    fuzzy_terms   = [primary_term] + [sq for sq in sci_queries if sq != primary_term]
    fuzzy_hits: list[dict] = []
    for ft in fuzzy_terms:
        fuzzy_hits.extend(_stage2_fuzzy(ft))

    # Stage 3: semantic — single batched forward pass over all unique queries
    semantic_queries = list(dict.fromkeys([primary_term] + sci_queries))
    semantic_hits    = _stage3_semantic_multi(semantic_queries)

    # Merge: keep best score per GDC field
    all_cands: dict[str, dict] = {}
    for r in fuzzy_hits + semantic_hits:
        f = r["gdc_field"]
        if f not in all_cands or r["score"] > all_cands[f]["score"]:
            all_cands[f] = r
    candidates = sorted(all_cands.values(), key=lambda x: x["score"], reverse=True)
    best = candidates[0] if candidates else None

    # Agreement check: best fuzzy and best semantic must point to the same field.
    # Use max() over concatenated lists so multi-query ordering doesn't matter.
    best_fuzzy = max(fuzzy_hits, key=lambda x: x["score"]) if fuzzy_hits else None
    top_f = best_fuzzy["gdc_field"] if best_fuzzy else None
    top_s = semantic_hits[0]["gdc_field"] if semantic_hits else None  # already sorted
    agree = top_f is not None and top_f == top_s

    if best and best["score"] >= AUTO_THRESHOLD and agree:
        status = "auto"
    elif best and best["score"] >= REVIEW_THRESHOLD:
        status = "review"
    else:
        status = "unmatched"

    return {"gdc_field": best["gdc_field"] if best else None,
            "index_path": best["index_path"] if best else None,
            "score": best["score"] if best else 0.0,
            "stage": best["stage"] if best else "unmatched",
            "expanded": expanded,
            "sci_expanded": sci_queries[0] if sci_queries else None,
            "status": status, "column": col,
            "query_type": "clinical", "candidates": candidates[:10]}

def _match_all(df):
    return {col: _match_column(col) for col in df.columns}

def _compile_export_fields(mapping_results):
    return {col: r["gdc_field"] for col, r in mapping_results.items()
            if r.get("gdc_field") and r.get("status") in ("auto", "review")}

def _map_values(df, mapping_results):
    value_mappings = {}
    for col, result in mapping_results.items():
        gf = result.get("gdc_field")
        ip = result.get("index_path")
        if not gf or not ip or ip not in _gdc_index:
            continue
        enum_values = _gdc_index[ip].get("enum_values", [])
        if not enum_values:
            continue
        user_vals  = df[col].dropna().unique().tolist()
        col_map    = {}
        lower_map  = {v.lower(): v for v in enum_values}
        for uv in user_vals:
            s = str(uv)
            if s in enum_values:
                col_map[s] = {"gdc_value": s, "status": "auto"}
            elif s.lower() in lower_map:
                col_map[s] = {"gdc_value": lower_map[s.lower()], "status": "auto"}
            else:
                bm, sc, _ = rf_process.extractOne(s, enum_values, scorer=fuzz.token_sort_ratio)
                if sc >= 85:
                    col_map[s] = {"gdc_value": bm, "status": "auto", "score": sc / 100}
                else:
                    top3 = rf_process.extract(s, enum_values, limit=3)
                    col_map[s] = {"gdc_value": None, "status": "review",
                                  "candidates": [m for m, _, _ in top3],
                                  "enum_values": enum_values}
        value_mappings[col] = {"gdc_field": gf, "index_path": ip,
                               "value_map": col_map,
                               "needs_review": any(v["status"] == "review"
                                                   for v in col_map.values())}
    return value_mappings

def _apply_manual_overrides(mapping_results, value_mappings):
    """Hard-coded corrections for known columns that the pipeline mis-matches."""
    if "crcdeath" in mapping_results:
        mapping_results["crcdeath"]["gdc_field"]  = "demographic.cause_of_death"
        mapping_results["crcdeath"]["index_path"] = "demographic.cause_of_death"
        value_mappings["crcdeath"] = {
            "gdc_field": "demographic.cause_of_death",
            "index_path": "demographic.cause_of_death", "needs_review": False,
            "value_map": {
                "1.0": {"gdc_value": "Cancer Related",     "status": "auto"},
                "0.0": {"gdc_value": "Not Cancer Related", "status": "auto"},
                "1":   {"gdc_value": "Cancer Related",     "status": "auto"},
                "0":   {"gdc_value": "Not Cancer Related", "status": "auto"},
            },
        }
    if "gradedifferentiation" in mapping_results:
        mapping_results["gradedifferentiation"]["gdc_field"]  = "diagnoses.tumor_grade"
        mapping_results["gradedifferentiation"]["index_path"] = "diagnosis.tumor_grade"
        value_mappings["gradedifferentiation"] = {
            "gdc_field": "diagnoses.tumor_grade", "index_path": "diagnosis.tumor_grade",
            "needs_review": False,
            "value_map": {
                "0.0": {"gdc_value": "G1", "status": "auto"},
                "1.0": {"gdc_value": "G3", "status": "auto"},
                "0":   {"gdc_value": "G1", "status": "auto"},
                "1":   {"gdc_value": "G3", "status": "auto"},
            },
        }
    if "ecogperformance_status" in mapping_results:
        value_mappings["ecogperformance_status"] = {
            "gdc_field": "follow_ups.ecog_performance_status",
            "index_path": "follow_up.ecog_performance_status", "needs_review": False,
            "value_map": {
                "Fully active":                  {"gdc_value": "0", "status": "auto"},
                "Restricted strenuous activity": {"gdc_value": "1", "status": "auto"},
                "Limited selfcare":              {"gdc_value": "2", "status": "auto"},
            },
        }
    if "location" in mapping_results:
        mapping_results["location"]["gdc_field"]  = "diagnoses.tissue_or_organ_of_origin"
        mapping_results["location"]["index_path"] = "diagnosis.tissue_or_organ_of_origin"
        value_mappings["location"] = {
            "gdc_field": "diagnoses.tissue_or_organ_of_origin",
            "index_path": "diagnosis.tissue_or_organ_of_origin", "needs_review": False,
            "value_map": {
                "Sigmoid colon":    {"gdc_value": "Sigmoid colon",            "status": "auto"},
                "Ascending colon":  {"gdc_value": "Ascending colon",          "status": "auto"},
                "Caecum":           {"gdc_value": "Cecum",                    "status": "auto"},
                "Transverse colon": {"gdc_value": "Transverse colon",         "status": "auto"},
                "Rectum":           {"gdc_value": "Rectum",                   "status": "auto"},
            },
        }

# ══════════════════════════════════════════════════════════════════════════════
# FIELD MATCHING  — Panel tab
# ══════════════════════════════════════════════════════════════════════════════

_fm_run_btn    = pn.widgets.Button(name="▶  Run Field Matching",
                                   button_type="primary", width=200)
_fm_status     = pn.pane.Markdown("")
_fm_summary    = pn.Row(visible=False)
_fm_results    = pn.Column(visible=False)
_fm_confirm    = pn.widgets.Button(name="Confirm & proceed to Query Builder →",
                                   button_type="success", width=300, visible=False)
_fm_overrides  = {}   # col → Select widget; read on confirm

def _stat_card(emoji, label, count, bg):
    return pn.pane.HTML(
        f"<div style='background:{bg};border-radius:8px;padding:12px 20px;"
        f"text-align:center;min-width:110px'>"
        f"<div style='font-size:22px'>{emoji}</div>"
        f"<div style='font-size:24px;font-weight:bold'>{count}</div>"
        f"<div style='font-size:12px;color:#555'>{label}</div></div>"
    )

def _build_fm_table(mapping_results):
    header = pn.Row(
        pn.pane.HTML("<b>Status</b>",    width=60,  styles={"color": "#555"}),
        pn.pane.HTML("<b>Column</b>",    width=185, styles={"color": "#555"}),
        pn.pane.HTML("<b>Expanded as</b>", width=185, styles={"color": "#555"}),
        pn.pane.HTML("<b>GDC field</b>", width=370, styles={"color": "#555"}),
        pn.pane.HTML("<b>Score</b>",     width=65,  styles={"color": "#555"}),
        pn.pane.HTML("<b>Stage</b>",     width=90,  styles={"color": "#555"}),
        styles={"background": "#f0f2f5", "padding": "8px 14px",
                "border-radius": "6px 6px 0 0", "border-bottom": "2px solid #d0d4db"},
    )
    rows = []
    for col, result in mapping_results.items():
        status = result.get("status")
        qtype  = result.get("query_type", "clinical")
        if qtype == "genomic":
            badge, bg = "🧬", "#f0f7ff"
        elif status == "auto":
            badge, bg = "✅", "#f0fff4"
        elif status == "review":
            badge, bg = "⚠️", "#fffbf0"
        else:
            badge, bg = "❌", "#fff5f5"

        candidates = result.get("candidates", [])
        if qtype == "genomic":
            field_w = pn.pane.HTML(
                f"<span style='font-family:monospace;font-size:12px;color:#555'>"
                f"🧬 {result.get('gene_symbol','—')} (genomic)</span>", width=360)
        elif candidates:
            # Map each option to (gdc_field, index_path) so overrides keep the
            # GDC index lookup (enum_values, type) in sync with the chosen field.
            opts = {f"{c['gdc_field']}  [{c['score']:.0%}, {c['stage']}]":
                        (c["gdc_field"], c["index_path"]) for c in candidates}
            top    = result.get("gdc_field")
            top_ip = result.get("index_path")
            if top and top not in [v[0] for v in opts.values()]:
                opts = {f"{top}  [top match]": (top, top_ip), **opts}
            default = (top, top_ip) if top else next(iter(opts.values()))
            sel = pn.widgets.Select(options=opts, value=default, width=360)
            _fm_overrides[col] = sel
            field_w = sel
        else:
            field_w = pn.pane.HTML(
                "<span style='color:#aaa;font-size:12px'>— no match found —</span>",
                width=360)

        expanded     = result.get("expanded", col)
        sci_expanded = result.get("sci_expanded")
        if expanded != col:
            exp_label = expanded
        elif sci_expanded:
            exp_label = f"🔬 {sci_expanded}"
        else:
            exp_label = None
        exp_html = (f"<span style='font-family:monospace;font-size:12px;color:#888'>"
                    f"{exp_label}</span>" if exp_label
                    else "<span style='color:#ccc;font-size:12px'>—</span>")
        sc       = result.get("score", 0)
        sc_col   = "#2d7a2d" if sc >= 0.8 else "#b8860b" if sc >= 0.5 else "#cc0000"

        rows.append(pn.Row(
            pn.pane.HTML(badge, width=60),
            pn.pane.HTML(f"<code style='font-size:12px'>{col}</code>", width=185),
            pn.pane.HTML(exp_html, width=185),
            field_w,
            pn.pane.HTML(f"<b style='color:{sc_col}'>{sc:.0%}</b>", width=65),
            pn.pane.HTML(f"<span style='font-size:12px;color:#666'>"
                         f"{result.get('stage','—')}</span>", width=90),
            styles={"background": bg, "padding": "5px 14px",
                    "border-bottom": "1px solid #e8eaed", "align-items": "center"},
        ))
    return pn.Column(
        header, *rows,
        styles={"border": "1px solid #d0d4db", "border-radius": "6px", "overflow": "hidden"},
    )

def _on_run_matching(event):
    df = state.get("cleaned_df")
    if df is None:
        df = state.get("df")
    if df is None:
        _fm_status.object = "❌ Upload a CSV and run Analysis first."
        return
    _fm_overrides.clear()
    _fm_status.object = "*⏳ Loading model — please wait…*"

    ok = _ensure_model(status_cb=lambda m: setattr(_fm_status, "object", m))
    if not ok:
        return

    try:
        _fm_status.object = "*⏳ Matching columns to GDC fields…*"
        mr  = _match_all(df)
        ef  = _compile_export_fields(mr)
        vm  = _map_values(df, mr)
        _apply_manual_overrides(mr, vm)

        state["mapping_results"] = mr
        state["export_fields"]   = ef
        state["value_mappings"]  = vm

        auto      = sum(1 for r in mr.values() if r["status"] == "auto")
        review    = sum(1 for r in mr.values() if r["status"] == "review")
        unmatched = sum(1 for r in mr.values() if r["status"] == "unmatched")
        genomic   = sum(1 for r in mr.values() if r.get("query_type") == "genomic")

        _fm_summary.objects = [
            _stat_card("✅", "Auto-matched", auto,      "#f0fff4"),
            _stat_card("⚠️", "Needs review", review,    "#fffbf0"),
            _stat_card("❌", "Unmatched",    unmatched, "#fff5f5"),
            _stat_card("🧬", "Genomic",      genomic,   "#f0f7ff"),
        ]
        _fm_summary.visible = True
        _fm_results.objects = [_build_fm_table(mr)]
        _fm_results.visible = True
        _fm_status.object   = (
            f"**Matching complete** — {len(mr)} columns processed. "
            "Review ⚠️ rows and correct any wrong GDC fields, then confirm."
        )
        _fm_confirm.visible = True
    except Exception as e:
        import traceback
        traceback.print_exc()
        _fm_status.object = f"❌ Field matching failed: `{e}`"

_fm_run_btn.on_click(_on_run_matching)

def _on_confirm(event):
    for col, sel in _fm_overrides.items():
        if col in state.get("mapping_results", {}):
            gdc_field, index_path = sel.value
            state["mapping_results"][col]["gdc_field"]  = gdc_field
            state["mapping_results"][col]["index_path"] = index_path
    _fm_status.object = "**✓ Mappings confirmed — proceeding to Query Builder.**"
    tabs.active = 3

_fm_confirm.on_click(_on_confirm)

field_matching_tab = pn.Column(
    pn.pane.HTML(
        "<h2>Field Matching</h2>"
        "<p style='color:#555'>Match your CSV columns to GDC clinical fields. "
        "Auto-matched ✅ columns are ready. Use the dropdowns on ⚠️ rows to correct "
        "wrong matches, then confirm to proceed to the Query Builder.</p>"
    ),
    _fm_run_btn,
    _fm_status,
    pn.layout.Spacer(height=8),
    _fm_summary,
    pn.layout.Spacer(height=8),
    pn.Column(_fm_results, scroll=True, height=500),
    pn.layout.Spacer(height=12),
    _fm_confirm,
    width=1060,
)

# ══════════════════════════════════════════════════════════════════════════════
# QUERY BUILDER  — backend
# ══════════════════════════════════════════════════════════════════════════════

CASES_ENDPT = "https://api.gdc.cancer.gov/cases"
MT_VALUES   = {"mt", "mut", "mutant", "mutated", "positive", "yes", "present", "1", "true"}
WT_VALUES   = {"wt", "wild_type", "wildtype", "wild type", "negative", "no", "absent", "0", "false"}

DEFAULT_EXPORT_FIELDS = [
    "case_id", "submitter_id", "primary_site", "disease_type", "project.project_id",
    "demographic.sex_at_birth", "demographic.vital_status", "demographic.age_at_index",
    "demographic.days_to_death", "demographic.race", "demographic.ethnicity",
    "diagnoses.age_at_diagnosis", "diagnoses.primary_diagnosis", "diagnoses.tumor_grade",
    "diagnoses.prior_malignancy",
    "diagnoses.pathology_details.lymphatic_invasion_present",
    "diagnoses.pathology_details.vascular_invasion_present",
    "follow_ups.ecog_performance_status",
    "follow_ups.molecular_tests.gene_symbol",
    "follow_ups.molecular_tests.test_result",
]

def _ensure_index():
    """Load the GDC index, building it from the live repo if not yet cached."""
    global _gdc_index
    if _gdc_index is not None:
        return True
    try:
        if not _INDEX_PATH.exists():
            build_gdc_index(clone_dir=_CLONE_DIR, schemas_dir=_SCHEMAS_DIR, output_path=_INDEX_PATH)
        with open(_INDEX_PATH) as f:
            _gdc_index = json.load(f)
        return True
    except Exception:
        return False

def _all_gdc_api_paths():
    """Return sorted list of all GDC API field paths from the index."""
    if not _ensure_index():
        return []
    seen = set()
    paths = []
    for ip in _gdc_index:
        ap = _to_api_path(ip)
        if ap not in seen:
            seen.add(ap)
            paths.append(ap)
    return sorted(paths)

def _map_val_qb(col, raw_val, value_mappings):
    vm    = (value_mappings or {}).get(col, {}).get("value_map", {})
    entry = vm.get(str(raw_val))
    if entry and entry.get("gdc_value"):
        return entry["gdc_value"]
    return str(raw_val)

def _build_clinical_filter_qb(conditions):
    clauses = []
    for cond in conditions:
        if cond.get("op") == "range":
            sub = []
            if cond.get("min") is not None:
                sub.append({"op": ">=", "content": {"field": cond["field"], "value": [cond["min"]]}})
            if cond.get("max") is not None:
                sub.append({"op": "<=", "content": {"field": cond["field"], "value": [cond["max"]]}})
            if sub:
                clauses.append(sub[0] if len(sub) == 1 else {"op": "and", "content": sub})
        else:
            vals = cond["values"] if isinstance(cond["values"], list) else [cond["values"]]
            clauses.append({"op": "in", "content": {"field": cond["field"], "value": vals}})
    if not clauses:
        return None
    return clauses[0] if len(clauses) == 1 else {"op": "and", "content": clauses}

def _fetch_case_ids(clinical_filter=None, gene_ids=None):
    """Paginate through all GDC hits (max 10 000 per page) and return the full case_id set."""
    PAGE = 10_000   # GDC hard maximum per request
    ids, from_idx = set(), 0
    while True:
        payload = {"fields": "case_id", "format": "JSON", "size": PAGE, "from": from_idx}
        if clinical_filter:
            payload["filters"] = clinical_filter
        if gene_ids:
            payload["case_filters"] = {
                "op": "in", "content": {"field": "genes.gene_id", "value": gene_ids}
            }
        r = requests.post(CASES_ENDPT,
                          headers={"Content-Type": "application/json"}, json=payload)
        r.raise_for_status()
        data  = r.json()["data"]
        hits  = data["hits"]
        ids  |= {h["case_id"] for h in hits}
        total = data.get("pagination", {}).get("total", len(ids))
        from_idx += PAGE
        if from_idx >= total or not hits:
            break
    return ids

def _fetch_full_cases(case_ids, fields, size=5000):
    # Always request "id" so we have a dedup key even if "case_id" is not returned.
    field_set = list(dict.fromkeys(["id", "case_id"] + list(fields)))
    all_hits  = []
    page_size = min(size, 2000)   # GDC performs better with ≤2000 per page
    from_idx  = 0
    while True:
        payload = {
            "filters": {"op": "in",
                        "content": {"field": "cases.case_id",
                                    "value": list(case_ids)}},
            "fields":  ",".join(field_set),
            "format":  "JSON",
            "size":    page_size,
            "from":    from_idx,
        }
        # Pass size/from as URL params too — some GDC versions need them there
        r = requests.post(
            f"{CASES_ENDPT}?size={page_size}&from={from_idx}",
            headers={"Content-Type": "application/json"},
            json=payload,
        )
        r.raise_for_status()
        data = r.json()["data"]
        hits = data["hits"]
        all_hits.extend(hits)
        total = data.get("pagination", {}).get("total", len(all_hits))
        from_idx += page_size
        if from_idx >= total or not hits:
            break
    return all_hits

def _get_nested(hit: dict, api_path: str):
    """Extract a value from a GDC hit using a dot-notation API path.

    GDC returns some entities as arrays (e.g. diagnoses, follow_ups,
    pathology_details, molecular_tests) and some as objects (demographic,
    project). We traverse the path generically: whenever the current node is a
    list we take the first element before continuing, so both structures work
    without needing a hardcoded entity-type registry.
    """
    parts = api_path.split(".")
    node  = hit
    for part in parts[:-1]:
        if node is None:
            return None
        if isinstance(node, list):
            node = node[0] if node else None
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    if node is None:
        return None
    if isinstance(node, list):
        node = node[0] if node else None
    if not isinstance(node, dict):
        return None
    return node.get(parts[-1])


def _flatten_gdc_hits(hits: list, fields: list) -> list:
    """Dynamically flatten GDC hits into rows using the caller-supplied field
    list.  Column names are the full API paths (e.g. diagnoses.tumor_grade).
    case_id is always included as the dedup key."""
    # Ensure case_id is always present regardless of what the user selected.
    all_fields = list(dict.fromkeys(["case_id"] + list(fields)))
    rows = []
    for hit in hits:
        uid = hit.get("case_id") or hit.get("id")
        row: dict = {"case_id": uid}
        for path in all_fields:
            if path == "case_id":
                continue
            row[path] = _get_nested(hit, path)
        rows.append(row)
    return rows

def _run_gdc_query(filter_spec, export_fields=None):
    """
    Execute a GDC query from a {col: spec} filter_spec, where spec is either
    {"kind": "multi", "values": [...]} (OR'd "in" match) or
    {"kind": "range", "min": x, "max": y} (inclusive numeric range).
    Returns (DataFrame, list_of_log_lines).
    """
    mr = state.get("mapping_results") or {}
    vm = state.get("value_mappings")  or {}
    ef = export_fields or DEFAULT_EXPORT_FIELDS

    clinical_conditions, mt_gene_ids, wt_genes, log = [], [], [], []

    for col, spec in filter_spec.items():
        result = mr.get(col)
        if not result:
            log.append(f"⚠️ `{col}` not in mapping results — skipped"); continue
        if result.get("status") == "unmatched":
            log.append(f"⚠️ `{col}` unmatched — skipped"); continue

        if result.get("query_type") == "genomic":
            raw_vals = spec.get("values") or []
            if not raw_vals:
                continue
            nv = str(raw_vals[0]).lower().strip().replace(" ", "_")
            if nv in MT_VALUES:
                mt_gene_ids.append(result["ensembl_id"])
                log.append(f"🧬 `{col}` ({result['gene_symbol']}) → MT inclusion")
            elif nv in WT_VALUES:
                wt_genes.append((result["ensembl_id"], result["gene_symbol"]))
                log.append(f"🧬 `{col}` ({result['gene_symbol']}) → WT subtraction")
            continue

        gf = result.get("gdc_field")
        if not gf:
            log.append(f"❌ `{col}` has no GDC field"); continue

        if spec.get("kind") == "range":
            lo, hi = spec["min"], spec["max"]
            clinical_conditions.append({"field": gf, "op": "range", "min": lo, "max": hi})
            log.append(f"✅ `{col}` → `{gf}` between {lo:g} and {hi:g}")
        else:
            raw_vals = spec.get("values") or []
            gdc_vals = [_map_val_qb(col, v, vm) for v in raw_vals]
            clinical_conditions.append({"field": gf, "op": "in", "values": gdc_vals})
            log.append(f"✅ `{col}` → `{gf}` in {gdc_vals} (OR'd)")

    cf = _build_clinical_filter_qb(clinical_conditions)
    log.append(f"\n*Querying GDC — {len(clinical_conditions)} clinical + "
               f"{len(mt_gene_ids)} MT gene filter(s)…*")
    try:
        ids = _fetch_case_ids(clinical_filter=cf,
                              gene_ids=mt_gene_ids if mt_gene_ids else None)
        log.append(f"→ {len(ids)} candidates")

        for ens_id, sym in wt_genes:
            mut_ids = _fetch_case_ids(clinical_filter=cf, gene_ids=[ens_id])
            before  = len(ids)
            ids -= mut_ids
            log.append(f"→ Removed {len(mut_ids)} {sym}-mutant ({before}→{len(ids)})")

        if not ids:
            log.append("ℹ️ No cases matched — try relaxing the filters.")
            return pd.DataFrame(), log

        hits   = _fetch_full_cases(ids, ef)
        df_out = pd.DataFrame(_flatten_gdc_hits(hits, ef))
        if not df_out.empty and "case_id" in df_out.columns:
            df_out = df_out.drop_duplicates(subset="case_id")
        log.append(f"**✅ {len(df_out)} cases returned.**")
        return df_out, log

    except requests.RequestException as e:
        log.append(f"❌ GDC API error: `{e}`")
        return pd.DataFrame(), log

# ══════════════════════════════════════════════════════════════════════════════
# QUERY BUILDER  — Panel tab
# ══════════════════════════════════════════════════════════════════════════════

# ── Export fields selector ────────────────────────────────────────────────────
# Eagerly load the GDC index (just JSON parsing — fast) so the MultiChoice search
# works immediately without requiring a button click.
_qb_all_paths: list = []
if _ensure_index():
    _qb_all_paths = _all_gdc_api_paths()

_qb_export_choice = pn.widgets.MultiChoice(
    name="Fields to include in results (type to search — shows all GDC fields)",
    options=_qb_all_paths,
    value=[p for p in DEFAULT_EXPORT_FIELDS if p in _qb_all_paths] or DEFAULT_EXPORT_FIELDS[:],
    sizing_mode="stretch_width",
    option_limit=100,
    search_option_limit=100,
)
_qb_fields_status = pn.pane.Markdown(
    f"*{len(_qb_all_paths)} GDC fields available.*" if _qb_all_paths
    else "⚠️ GDC field index not yet built — it will be downloaded automatically when Field Matching runs."
)

# ── Per-cluster filter cards ──────────────────────────────────────────────────
_qb_clusters_col  = pn.Column()
_qb_load_btn      = pn.widgets.Button(
    name="▶  Load cluster filters", button_type="primary", width=200,
)
_qb_load_status   = pn.pane.Markdown("")
_qb_run_all_btn   = pn.widgets.Button(
    name="▶  Run all cluster queries", button_type="success",
    width=230, visible=False,
)
_qb_run_status    = pn.pane.Markdown("")
_qb_results_col   = pn.Column(visible=False)

# Stores per-cluster widget refs: {c_id: {"toggles": {col: Toggle}, "selects": {col: Select}}}
_qb_cluster_widgets = {}

def _csv_download_btn(get_df, filename, label="⬇ CSV", width=110, visible=False):
    """A FileDownload button that lazily serialises get_df() to CSV bytes on click."""
    def _cb():
        df = get_df()
        if df is None or df.empty:
            df = pd.DataFrame()
        return io.BytesIO(df.to_csv(index=False).encode("utf-8"))
    return pn.widgets.FileDownload(
        callback=_cb, filename=filename, button_type="default",
        label=label, width=width, visible=visible,
    )

def _build_filter_spec(togs, sels):
    """Read the include toggles + value widgets for a cluster into a {col: spec} dict
    consumable by _run_gdc_query (spec is {"kind": "multi"/"range", ...})."""
    filter_spec = {}
    for col, tog in togs.items():
        if not tog.value:
            continue
        kind, w = sels[col]["kind"], sels[col]["widget"]
        if kind == "range":
            lo, hi = w.value
            filter_spec[col] = {"kind": "range", "min": lo, "max": hi}
        elif kind == "multi":
            if not w.value:
                continue
            filter_spec[col] = {"kind": "multi", "values": list(w.value)}
        else:
            filter_spec[col] = {"kind": "multi", "values": [w.value]}
    return filter_spec

def _make_cluster_card(c_id, features):
    """
    Build a card for one cluster with include/exclude toggles and value dropdowns.
    features = [(col, dominant_value), ...]
    """
    mr = state.get("mapping_results") or {}
    df = state.get("cleaned_df")

    toggles = {}
    selects  = {}
    rows     = []

    for col, dom_val in features:
        result    = mr.get(col, {})
        status    = result.get("status", "unmatched")
        qtype     = result.get("query_type", "clinical")
        gdc_field = result.get("gdc_field") or result.get("gene_symbol") or "—"

        if status == "unmatched":
            badge = "❌"
        elif qtype == "genomic":
            badge = "🧬"
        elif status == "review":
            badge = "⚠️"
        else:
            badge = "✅"

        # Include / exclude toggle
        tog = pn.widgets.Toggle(
            name="Include", value=True,
            button_type="success", width=90,
        )
        def _make_tog_watcher(t):
            def _w(ev):
                t.name        = "Include" if ev.new else "Exclude"
                t.button_type = "success" if ev.new else "warning"
            return _w
        tog.param.watch(_make_tog_watcher(tog), "value")
        toggles[col] = tog

        # Value widget — choice depends on the *matched GDC field*'s type:
        #   • genomic         → MT / WT single-select
        #   • enumerated      → multi-select (OR'd via "in") using the GDC index's
        #                       authoritative enum_values (not the user's raw data)
        #   • numeric/range   → min/max range slider (querying an exact float is useless)
        #   • fallback        → multi-select built from the user's own data values
        index_path = result.get("index_path")
        gdc_meta   = _gdc_index.get(index_path, {}) if (index_path and _gdc_index) else {}
        gdc_opts   = gdc_meta.get("enum_values", [])
        gdc_type   = str(gdc_meta.get("type", ""))

        series = None
        if df is not None and col in df.columns:
            series = pd.to_numeric(df[col], errors="coerce").dropna()

        is_continuous = (
            qtype != "genomic" and not gdc_opts
            and (gdc_type in ("number", "integer")
                 or (series is not None and not series.empty
                     and pd.api.types.is_numeric_dtype(df[col])))
        )

        if qtype == "genomic":
            opts = ["MT", "WT"]
            default = str(dom_val) if str(dom_val) in opts else opts[0]
            val_widget = pn.widgets.Select(options=opts, value=default, width=160)
            kind = "single"
        elif is_continuous:
            if series is not None and not series.empty:
                lo, hi = float(series.min()), float(series.max())
            else:
                lo, hi = 0.0, 100.0
            if lo == hi:
                lo, hi = lo - 1, hi + 1
            is_int = gdc_type == "integer" or pd.api.types.is_integer_dtype(df[col]) \
                if (df is not None and col in df.columns) else gdc_type == "integer"
            step = 1.0 if is_int else max((hi - lo) / 100, 0.01)
            start, end = (np.floor(lo), np.ceil(hi)) if is_int else (lo, hi)
            val_widget = pn.widgets.RangeSlider(
                start=start, end=end, value=(lo, hi), step=step, width=240,
            )
            kind = "range"
        else:
            if gdc_opts:
                opts = sorted(str(v) for v in gdc_opts)
            elif df is not None and col in df.columns:
                opts = sorted(df[col].dropna().unique().astype(str).tolist())
            else:
                opts = [str(dom_val)]
            default = [str(dom_val)] if str(dom_val) in opts else (opts[:1] if opts else [str(dom_val)])
            val_widget = pn.widgets.MultiChoice(
                options=opts, value=default, width=220, height=80,
                placeholder="Select one or more (OR'd together)",
            )
            kind = "multi"

        selects[col] = {"kind": kind, "widget": val_widget}

        row = pn.Row(
            tog,
            pn.pane.HTML(f"<span style='font-size:12px'>{badge}</span>", width=24),
            pn.pane.HTML(
                f"<code style='font-size:12px'>{col}</code>", width=175,
            ),
            val_widget,
            pn.pane.HTML(
                f"<span style='color:#666;font-size:11px;font-family:monospace'>"
                f"→ {gdc_field}</span>",
                width=300,
            ),
            align="center",
        )
        rows.append(row)

    # Per-cluster run button + result display + CSV export
    run_btn    = pn.widgets.Button(name=f"▶  Run cluster {c_id}", button_type="primary", width=160)
    dl_btn     = _csv_download_btn(
        lambda cid=c_id: (state.get("query_results") or {}).get(str(cid)),
        filename=f"cluster_{c_id}_gdc_results.csv", label="⬇ Export CSV", width=130,
    )
    c_status   = pn.pane.Markdown("")
    c_result   = pn.Column(visible=False)

    def _make_cluster_runner(cid, togs, sels, st_md, res_col, dl):
        def _run(event):
            st_md.object = f"*⏳ Querying GDC for cluster {cid}…*"
            dl.visible = False
            filter_spec = _build_filter_spec(togs, sels)
            if not filter_spec:
                st_md.object = "⚠️ No features selected — tick at least one and choose a value."
                return
            ef  = _qb_export_choice.value or DEFAULT_EXPORT_FIELDS
            df_r, log = _run_gdc_query(filter_spec, export_fields=ef)
            st_md.object = "\n\n".join(log)
            state.setdefault("query_results", {})[str(cid)] = df_r
            if not df_r.empty:
                tbl = pn.widgets.Tabulator(
                    df_r, pagination="local", page_size=10,
                    sizing_mode="stretch_width", name=f"Cluster {cid} results",
                )
                res_col.objects = [tbl]
                res_col.visible = True
                dl.visible = True
        return _run

    run_btn.on_click(_make_cluster_runner(c_id, toggles, selects, c_status, c_result, dl_btn))

    card = pn.Column(
        pn.pane.HTML(
            f"<b style='font-size:14px'>Cluster {c_id}</b>"
            f"<span style='color:#666;font-size:12px;margin-left:8px'>"
            f"{len(features)} defining feature(s)</span>"
        ),
        pn.Row(
            pn.pane.HTML("<b style='font-size:11px;color:#555'>Include</b>", width=90),
            pn.pane.HTML("", width=24),
            pn.pane.HTML("<b style='font-size:11px;color:#555'>Column</b>", width=175),
            pn.pane.HTML("<b style='font-size:11px;color:#555'>Value (OR / range)</b>", width=240),
            pn.pane.HTML("<b style='font-size:11px;color:#555'>GDC field</b>", width=300),
        ),
        pn.layout.Divider(margin=(4, 0)),
        *rows,
        pn.layout.Spacer(height=8),
        pn.Row(run_btn, dl_btn, c_status),
        c_result,
        styles={"border": "1px solid #d0d4db", "border-radius": "6px",
                "padding": "14px", "margin": "8px 0", "background": "#fff"},
    )
    return card, toggles, selects

def _on_load_cluster_filters(event):
    cdf = state.get("cluster_defining_features")
    mr  = state.get("mapping_results")
    if not cdf:
        _qb_load_status.object = (
            "⚠️ No cluster definitions found — complete Analysis → Cluster Signatures first."
        )
        return
    if not mr:
        _qb_load_status.object = (
            "⚠️ Field matching not yet run — complete Field Matching first."
        )
        return

    # Seed export fields from confirmed field-matching results (matched fields only),
    # keeping any already-selected values the user may have manually added.
    matched_paths = [
        r["gdc_field"] for r in mr.values()
        if r.get("gdc_field") and r.get("status") in ("auto", "review")
        and r.get("query_type") != "genomic"
        and r["gdc_field"] in _qb_all_paths
    ]
    existing = set(_qb_export_choice.value)
    new_value = list(dict.fromkeys(matched_paths + [p for p in DEFAULT_EXPORT_FIELDS if p in _qb_all_paths]))
    _qb_export_choice.value = [p for p in new_value if p in _qb_all_paths or p in existing]

    _qb_clusters_col.clear()
    _qb_cluster_widgets.clear()

    for c_id, features in sorted(cdf.items(), key=lambda x: x[0]):
        if not features:
            continue
        card, togs, sels = _make_cluster_card(c_id, features)
        _qb_clusters_col.append(card)
        _qb_cluster_widgets[c_id] = {"toggles": togs, "selects": sels}

    if _qb_cluster_widgets:
        _qb_run_all_btn.visible = True
        _qb_load_status.object = (
            f"*{len(_qb_cluster_widgets)} cluster(s) loaded — "
            "adjust filters and run individual or all queries.*"
        )
    else:
        _qb_load_status.object = "⚠️ No clusters with features found."

_qb_load_btn.on_click(_on_load_cluster_filters)

def _on_run_all(event):
    _qb_run_status.object = "*⏳ Running all cluster queries…*"
    ef = _qb_export_choice.value or DEFAULT_EXPORT_FIELDS
    logs_all = []
    for c_id, widgets_d in _qb_cluster_widgets.items():
        togs = widgets_d["toggles"]
        sels = widgets_d["selects"]
        filter_spec = _build_filter_spec(togs, sels)
        if not filter_spec:
            continue
        df_r, log = _run_gdc_query(filter_spec, export_fields=ef)
        state.setdefault("query_results", {})[str(c_id)] = df_r
        logs_all.append(f"**Cluster {c_id}** — {len(df_r)} cases")

    if logs_all:
        _qb_run_status.object = "**All queries complete:**\n\n" + "\n\n".join(logs_all)
        _qb_results_col.visible = True
        _qb_results_col.objects = [_build_qb_summary()]
    else:
        _qb_run_status.object = "⚠️ No filter specs built — select at least one feature per cluster."

def _build_qb_summary():
    """Row-based summary table: one row per cluster (with a CSV export button each),
    plus a final combined-export row covering all clusters' deduplicated cases."""
    qr = state.get("query_results") or {}
    header = pn.Row(
        pn.pane.HTML("<b style='font-size:11px;color:#555'>Cluster</b>",        width=200),
        pn.pane.HTML("<b style='font-size:11px;color:#555'>Cases returned</b>", width=140),
        pn.pane.HTML("<b style='font-size:11px;color:#555'>Export</b>",         width=140),
    )
    rows = []
    for c_id, df_r in qr.items():
        dl = _csv_download_btn(
            lambda df=df_r: df, filename=f"cluster_{c_id}_gdc_results.csv",
            label="⬇ CSV", width=110, visible=not df_r.empty,
        )
        rows.append(pn.Row(
            pn.pane.HTML(f"Cluster {c_id}", width=200),
            pn.pane.HTML(f"{len(df_r):,}", width=140),
            dl,
            align="center",
        ))

    non_empty = [df for df in qr.values() if not df.empty]
    if non_empty:
        combined = pd.concat(non_empty, ignore_index=True)
        if "case_id" in combined.columns:
            combined = combined.drop_duplicates(subset="case_id")
    else:
        combined = pd.DataFrame()
    combined_dl = _csv_download_btn(
        lambda df=combined: df, filename="all_clusters_gdc_results.csv",
        label="⬇ CSV", width=110, visible=not combined.empty,
    )
    rows.append(pn.Row(
        pn.pane.HTML("<b>All clusters (combined, deduplicated)</b>", width=200),
        pn.pane.HTML(f"<b>{len(combined):,}</b>", width=140),
        combined_dl,
        align="center",
    ))

    return pn.Column(
        pn.pane.HTML("<b>Summary</b>"),
        header,
        pn.layout.Divider(margin=(2, 0)),
        *rows,
        styles={"border": "1px solid #d0d4db", "border-radius": "6px",
                "padding": "12px 14px", "margin": "8px 0", "background": "#fff"},
    )

_qb_run_all_btn.on_click(_on_run_all)

query_builder_tab = pn.Column(
    pn.pane.HTML(
        "<h2>Query Builder</h2>"
        "<p style='color:#555'>Select the GDC fields to export, review the pre-populated "
        "cluster filters, then run the queries to retrieve matched cases from the GDC.</p>"
    ),
    _card(
        _step_header("A", "Export Fields",
                     "Choose which GDC data fields to include in the downloaded results"),
        pn.pane.HTML(
            "<div style='background:#e8f4fd;border:1px solid #90caf9;border-radius:6px;"
            "padding:10px 14px;margin-bottom:8px;font-size:13px;color:#1a4a6b'>"
            "ℹ️ When you click <b>Load cluster filters</b> below, this list will be "
            "automatically populated with the fields matched in the "
            "<b>Field Matching</b> tab. You can add or remove fields here at any time "
            "before running a query."
            "</div>"
        ),
        _qb_fields_status,
        pn.layout.Spacer(height=8),
        _qb_export_choice,
    ),
    _card(
        _step_header("B", "Cluster Filters",
                     "Each cluster's defining features are pre-loaded as GDC filters — "
                     "adjust values or toggle features on/off"),
        pn.Row(_qb_load_btn, _qb_load_status, align="center"),
        pn.layout.Spacer(height=8),
        _qb_clusters_col,
        pn.layout.Spacer(height=8),
        pn.Row(_qb_run_all_btn, _qb_run_status, align="center"),
        pn.layout.Spacer(height=8),
        _qb_results_col,
    ),
    width=1060,
)

# ══════════════════════════════════════════════════════════════════════════════

tabs = pn.Tabs(
    ("📂  Upload",        upload_tab),
    ("🧬  Analysis",       analysis_tab),
    ("🔗  Field Matching", field_matching_tab),
    ("🔍  Query Builder",  query_builder_tab),
    dynamic=True,
)
tabs

# ──────────────────────────────────────────────────────────────────────────────
# Serve the app
# ──────────────────────────────────────────────────────────────────────────────

def serve_pdet_app():
    """Entry point for the `pdet` terminal command."""
    pn.serve(tabs, port=5006, show=True)

# Below has been deprecated
if __name__ == "__main__":
    serve_pdet_app()
