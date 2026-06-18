[![PyPI Version](https://img.shields.io/pypi/v/pdet)](https://pypi.org/project/pdet/)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.20748819.svg)](https://doi.org/10.5281/zenodo.20748819)


# Usage

## Install

1. Install uv:
- Open terminal
-- Windows Powershell: powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
-- WLS/macOS/Linux shell: curl -LsSf https://astral.sh/uv/install.sh | sh
- Close and open a new terminal

2. Clone/download repo from https://github.com/Jobot99/Patient-Data-Expander-Tool

3. Open terminal, navigate to the Patient Data Expander Tool directory and run 
```bash
uv sync
```
to install necessary packages. 

## Run

To run pdet use
```bash
uv run python -m pdet
```

## Input data tips

For the expander tool to analyse your patient data most effectively:
- Ensure your uploaded CSV file includes columns with full named headers rather than abbreviations (you can change these within the app too).
- Do not include derived columns i.e. "Colorectal Cancer" [Yes/No] derived from "Cancer Type" [Breast, Colorectal, Testicular...etc] as these will be unlikely to match to public databases.
- Ensure data is as complete as possible. There is a removal/imputation stage within the tool, but the more complete your data is, the better.
- Ensure that patient id column is named one of the following: "cprid", "patient_id", "patientid", "id", "subject_id".

# Acknowledgements 

Tool created by [Joseph Cottrell](https://github.com/Jobot99) under the supervision of [Ferran Cardoso](https://github.com/fercrcode) at the Integrated Pathology Unit.
