[![PyPI Version](https://img.shields.io/pypi/v/pdet)](https://pypi.org/project/pdet/)

# Usage

## Installation

You can install the **Patient Data Expander Tool** directly from PyPI using `pip`:

```bash
pip install pdet
```

## How to run

Once installed, you can run from your terminal by running:

```bash
run_pdet
```

## Input data tips

For the expander tool to analyse your patient data most effectively:
- Ensure your uploaded CSV file includes columns with full named headers rather than abbreviations (you can change these within the app too).
- Do not include derived columns i.e. "Colorectal Cancer" [Yes/No] derived from "Cancer Type" [Breast, Colorectal, Testicular...etc] as these will be unlikely to match to public databases.
- Ensure data is as complete as possible. There is a removal/imputation stage within the tool, but the more complete your data is, the better.

# Acknowledgements 

Tool created by [Joseph Cottrell](https://github.com/Jobot99) under the supervision of [Ferran Cardoso](https://github.com/fercrcode) at the Integrated Pathology Unit.
