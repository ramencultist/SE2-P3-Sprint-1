The selected requirements for the sprint 1 and now sprint 2 are contained in the [EECS 581-Project 3 Requirements Group 1.xlsx](EECS%20581-Project%203%20Requirements%20Group%201.xlsx)

Updated requirements artifacts for validation are contained in [EECS 581 Project 3 Architecture.docx](EECS%20581%20Project%203%20Architecture.docx)

The sprint 1 branch contains all the information for sprint 1 frozen for the 11/8/2026 deadline at 11:59 PM.


## Sprint 2

Sprint 2 adds requirements 7 (in-road hazard detection) and 9 (construction identification). Each acceptance
criterion is traced to its code and tests, and the demo steps are listed, in [docs/sprint2.md](docs/sprint2.md).

Quick start:

```bash
pip install -r requirements.txt
python -m pytest -q tests
python tools/demo_sprint2.py --check            # models and phone status
python tools/demo_sprint2.py <clip.mp4>         # CV pipeline -> phone -> MacroDroid -> Waze (dry run)
```
