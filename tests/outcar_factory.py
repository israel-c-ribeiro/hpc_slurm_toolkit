"""Synthetic OUTCAR files with the lines that run_status.py and submit_all.sh look for.

They reproduce the *format* of the relevant VASP lines (iteration headers,
'aborting loop ...', the ionic free-energy block, 'reached required accuracy',
the timing block), not a real calculation.
"""
from __future__ import annotations

HEADER = (" vasp.6.x  SYNTHETIC OUTCAR written by hpc_slurm_toolkit for tests and demos"
          " -- not a real calculation\n\n")
BAR = "-" * 39


def fake_outcar(energies: list[float], *, nsw: int = 0, ibrion: int = -1, nelm: int = 60,
                n_elec: int = 8, last_scf: str = "ok", ionic_converged: bool = False,
                finished: bool = True) -> str:
    """Build an OUTCAR text.

    energies         free energy (eV) of each ionic step
    last_scf         'ok'      last loop reached EDIFF
                     'bad'     VASP 6 message 'EDIFF was not reached (unconverged)'
                     'silent'  VASP 5 style: NELM iterations, no message
                     'running' last loop still running (killed): no message, no energy block
    ionic_converged  add 'reached required accuracy'
    finished         add the final timing block
    """
    out = [HEADER,
           f"   NELM   = {nelm:6d};   NELMIN=  2; NELMDL= -5     # of ELM steps\n",
           "   EDIFF  = 0.1E-05   stopping-criterion for ELM\n",
           f"   NSW    = {nsw:6d}    number of steps for IOM\n",
           f"   IBRION = {ibrion:6d}    ionic relax: 0-MD 1-quasi-New 2-CG\n\n"]
    for k, energy in enumerate(energies, start=1):
        last = k == len(energies)
        mode = last_scf if last else "ok"
        n_iter = nelm if mode == "silent" else n_elec
        for it in range(1, n_iter + 1):
            out.append(f" {BAR} Iteration {k:6d}({it:4d})  {BAR}\n")
            out.append(f"  free energy    TOTEN  = {energy + 0.1 / it:18.8f} eV\n\n")
        if mode == "running":
            break
        if mode == "ok":
            out.append(" ------------------------ aborting loop because EDIFF is reached "
                       "----------------------------------------\n\n")
        elif mode == "bad":
            out.append(" ------------------------ aborting loop EDIFF was not reached (unconverged)  "
                       "----------------------------\n\n")
        out.append("  FREE ENERGIE OF THE ION-ELECTRON SYSTEM (eV)\n"
                   "  ---------------------------------------------------\n"
                   f"  free  energy   TOTEN  = {energy:18.8f} eV\n\n"
                   f"  energy  without entropy= {energy:18.8f}  energy(sigma->0) = {energy:18.8f}\n\n")
    if ionic_converged:
        out.append(" reached required accuracy - stopping structural energy minimisation\n")
    if finished:
        out.append("\n\n General timing and accounting informations for this job:\n"
                   " ========================================================\n\n"
                   "                  Total CPU time used (sec):       12.345\n")
    return "".join(out)
