"""Generated software-only PSF-shaped fixtures; never circuit evidence."""
from pathlib import Path


def write_stb_pair(directory: Path, metrics: dict, *, probe="VLOOP", version="software-fixture"):
    directory.mkdir(parents=True, exist_ok=True)
    header = ('HEADER\n"PSFversion" "1.00"\n"simulator" "spectre"\n'
              f'"version" "{version}"\n"date" "software fixture only"\n'
              '"analysis type" "{kind}"\n"analysis name" "stb"\n'
              f'"operating point producer" "dcOp"\n"probe" "{probe}"\n')
    loop = directory / "stb.stb"
    margin = directory / "stb.margin.stb"
    loop.write_text(header.format(kind="stb") + 'TYPE\nVALUE\nEND\n', encoding="utf-8")
    gm, gf = metrics.get("gain_margin_db"), metrics.get("phase_crossover_frequency_hz")
    reason = ('"gainMarginInfo" "No gain margin. Phase does not reach 0 Deg"\n'
              if gm is None else '')
    margin.write_text(header.format(kind="margin.stb") + reason + 'TYPE\nVALUE\n'
        + f'"gainMargin" "dB" {gm if gm is not None else "nan"}\n'
        + f'"gainMarginFreq" "Hz" {gf if gf is not None else "nan"}\n'
        + f'"phaseMargin" "Deg" {metrics["phase_margin_deg"]} PROP(\n'
        + '"phaseMarginInfo" "software fixture only"\n)\n'
        + f'"phaseMarginFreq" "Hz" {metrics["loop_unity_gain_frequency_hz"]}\n'
        + '"stb_state" "stbstate" "software fixture, no stability claim"\nEND\n', encoding="utf-8")
    return loop, margin
