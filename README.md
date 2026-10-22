# BOM & Routing Generator

Upload a **Product Attribute Master** (product structure) → download the SAP **BOM** and
**routing** as CSV, in the same column layout as the SAP extracts. Every run is kept, so
previous parts can be re-downloaded later.

## Run (Command Prompt)

```
cd /d C:\Users\Sid\Claude\Projects\steel1new\bom_routing_generator
pip install flask openpyxl
python bom_routing_app.py
```

The browser opens at http://127.0.0.1:5003. Enter the part name, choose the master
(.xlsx, .xlsm or .csv), click **Generate BOM & Routing**, then download
`BOM_<part>.csv` and `ROUTING_<part>.csv`. Generating the same part again creates v2, v3 …
(older versions stay downloadable). History, CSVs and the original masters are stored in
`bom_routing_data\` next to the app — back up or copy that folder to keep the history.

Without the UI: `python bom_routing_core.py master.xlsx --part 13883726 --out-dir out`

## What it tolerates in the master

* **Any number of levels** (0–3, 0–5, 0–7 …). Level columns are found from the data's
  staircase shape (each row's part number sits in exactly one of a run of adjacent
  columns), so they can be renamed freely; "lvl N" style headers are a back-up signal.
  Skipped levels and unused (empty) level columns are fine.
* **Loose headers** for the other columns: wording, brackets, case and small typos don't
  matter ("Qty", "Quantity", "Qty / Parent", "Quantiy" …; "Workcentre", "Work Center",
  "Work Ctr" …). A title row above the header, an S.No column, extra or reordered columns
  are fine.
* **Operations:** one row per operation (part repeated), continuation rows with the level
  cell left blank, or several operations in one cell separated by `;`.
  A numeric workcentre goes to *Work center*, a name goes to *Operation short text*; a name
  in an unlabeled column next to the workcentre number is picked up too.

## What gets filled

| Output | From the master | Left blank |
|---|---|---|
| BOM | explosion level, component number, qty, unit; description / item category if such columns exist | overflow, exception, requirement & stock segment (and anything not in the master) |
| Routing | material, activity (Op Seq as 0010), work center (if a number), operation short text; plant / description if such columns exist | task list type, group, counter, sequence, cost centre, standard values etc. |

Generated, not read: **BOM item numbers** (0010, 0020 … per parent, in sheet order).
Op Seq is converted as written (10 → 0010, 1 → 0001), never renumbered.

The result screen shows which columns were detected and any notes, e.g. rows with part
numbers in two level columns (left-most is used), several level-0 parts, or no Qty column.

Not supported: parent–child (adjacency) lists with a "Level" number column, and old .xls
files (save as .xlsx).
