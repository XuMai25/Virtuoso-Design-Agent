# Virtuoso Analog IC Design & Verification Agent

**Give your AI agent an analog design task in Cadence Virtuoso, from sizing and schematic capture through Spectre verification and ADE handoff.**

[View Virtuoso Analog IC Design & Verification Agent on Agensi](https://agensi.io/skills/virtuoso-analog-ic-design-verification-agent)

## One workflow across TSMC 28, 65 and 180 nm

**Read back → netlist → simulate → verify.** The same workflow checked each saved schematic, generated a native Virtuoso netlist and ran OP, DC, AC and transient.

**Task request**

> Read back the saved common-source stage in my TSMC 28, 65 and 180 nm libraries. Keep schematic and ADE read-only. Generate each native Virtuoso netlist, run the same OP, DC, AC and transient checks, and return one report verified against the original Spectre PSF.

### Spectre report

- **TSMC N28** · OP 0.4542 V · DC 0.3510 V · AC 12.090 dB / 251.637 MHz · delay 0.195 / 0.668 ns
- **TSMC 65 nm** · OP 0.5363 V · DC 0.5093 V · AC 11.715 dB / 366.018 MHz · delay 0.211 / 0.346 ns
- **TSMC 180 nm** · OP 0.9080 V · DC 0.5805 V · AC 20.827 dB / 125.530 MHz · delay 0.103 / 1.906 ns

**Switching PDKs** requires the installed libraries, model sections and device mappings for each process. The tested N28 width mapped from `Wfg` to `w`. The 65 and 180 nm devices used `w` directly. Each circuit used its own supply, bias and sizing.

*OP is output bias. DC is input at half-supply output. AC gives low-frequency gain and first −3 dB bandwidth. Fall and rise delays run from the input step midpoint to 0.4 × VDD at the output.*

**12 analyses checked against original PSF.** Skill v0.9.0 with public Bridge 1.0.0 at one licensed CAD site. Same topology, 27 °C and 50 fF load.

Virtuoso Analog IC Design & Verification Agent brings workflows from Virtuoso Design Agent (VDA) to your existing CAD environment. Bridge connects your agent to Virtuoso and Spectre. The pack guides the agent through the design steps, checks the saved circuit, and turns simulation results into a report you can review and reproduce. [Virtuoso Bridge](https://github.com/Arcadia-1/virtuoso-bridge-lite)

## What you can do with it

- **Design and size circuits.** Start from a design brief or an existing schematic. Characterize devices and compare sizing or bias candidates against your gain, bandwidth, power, and other limits.
- **Build and check schematics.** Create a schematic from specified connectivity or inspect an existing cellview. Read back the saved circuit to check instances, nets, pins, and device parameters.
- **Run and verify Spectre simulations.** Run operating-point, DC, AC, transient, noise, and single-loop stability analyses, or sweep selected parameters and PVT conditions. Review measured values alongside the executed netlist, logs, and raw PSF data.
- **Continue in ADE.** Run a saved setup, tie results to the exact run history, and pick up the work in Virtuoso for manual review or further analysis.

## Bring your own PDK

Start with a model setup that already runs at your site. Supply the pack with your design and technology libraries, Spectre model files and sections, and the device masters, terminals, and parameter mappings your circuit uses. Set supply, bias, stimulus, and load for the job.

The agent checks those inputs against the saved schematic and Virtuoso’s native netlist. For circuits that depend on extracted views or hierarchy-specific overrides, use your site’s saved ADE setup.

## Get the pack

Bring a licensed Virtuoso and Spectre environment, an installed PDK, and an agent with shell and file access. The pack includes six skills, runnable scripts, and configuration templates. Start with a read-only check on a familiar cell before moving on to design changes or larger sweeps.
