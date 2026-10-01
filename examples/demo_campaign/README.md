# Demo campaign

A tiny, public example to try the tools without any cluster or VASP licence:
three textbook structures relaxed with the same settings.

```text
demo_campaign/
├── structures/
│   ├── Si.vasp            diamond Si, primitive cell (2 atoms)
│   ├── NaCl.vasp          rock-salt NaCl, primitive cell (2 atoms)
│   └── Fe_bcc.vasp        bcc Fe, conventional cell (2 atoms)
├── INCAR                  template: PBE cell + ionic relaxation (ISIF = 3)
├── KPOINTS                Gamma-centred 8 x 8 x 8 mesh
└── fake_potcar_library/   Si/ Na_pv/ Cl/ Fe/  -- PLACEHOLDERS, see below
```

> **The POTCAR files here are placeholders, not real POTCARs.** They contain
> only a header and a `TITEL` line so that `make_job_folders.py` can show its
> species-order check. Real POTCARs are licensed by VASP Software GmbH and must
> not be redistributed: point `--potcar-dir` to your own licensed `potpaw_PBE`
> folder for real calculations.

## Try it (from the repository root)

```bash
# 1. plan only: checks every structure and POTCAR, writes nothing
python tools/make_job_folders.py examples/demo_campaign/structures --out runs \
    --incar examples/demo_campaign/INCAR --kpoints examples/demo_campaign/KPOINTS \
    --potcar-dir examples/demo_campaign/fake_potcar_library --potcar-map Na=Na_pv \
    --job-template slurm_templates/vasp_relax_autorestart.sh \
    --set ENCUT=520 --drop NCORE --dry-run
```

```text
folder  formula  atoms  POTCAR    action
------  -------  -----  --------  ------
Fe_bcc  Fe2      2      Fe        create
NaCl    NaCl     2      Na_pv Cl  create
Si      Si2      2      Si        create

dry run: 3 folder(s) planned in runs, nothing written
```

```bash
# 2. the same without --dry-run writes runs/<name>/ + runs/manifest.csv + runs/submit_all.sh

# 3. Fe is magnetic: add spin polarisation to that folder only
python tools/incar_tags.py runs/Fe_bcc --set ISPIN=2 --set "MAGMOM=2*2.5" --provenance

# 4. see what changed where
python tools/incar_tags.py "runs/*/INCAR" --get ENCUT --get ISPIN --get MAGMOM
```

Things to notice:

* `--potcar-map Na=Na_pv` selects the POTCAR variant; without it the tool
  looks for `fake_potcar_library/Na/` and stops with a clear message;
* swap two POTCARs (e.g. give a single Cl+Na POTCAR with `--potcar`) and
  nothing is written: the species order must match the POSCAR;
* the INCAR line `ISMEAR = 0 ; SIGMA = 0.05` carries two tags; both survive the
  edits;
* every generated INCAR starts with a comment saying where it came from.

The folders are complete VASP inputs apart from the placeholder POTCARs;
`runs/` is in `.gitignore`.
