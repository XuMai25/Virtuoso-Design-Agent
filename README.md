# Virtuoso Design Pack

**Give your AI agent an analog design task in Cadence Virtuoso, from sizing and schematic capture through Spectre verification and ADE handoff.**

Virtuoso Design Pack brings workflows from Virtuoso Design Agent (VDA) to your existing CAD environment. Bridge connects your agent to Virtuoso and Spectre. The pack guides the agent through the design steps, checks the saved circuit, and turns simulation results into a report you can review and reproduce. [Virtuoso Bridge](https://github.com/Arcadia-1/virtuoso-bridge-lite)

## What you can do with it

- **Design and size circuits.** Start from a design brief or an existing schematic. Characterize devices and compare sizing or bias candidates against your gain, bandwidth, power, and other limits.
- **Build and check schematics.** Create a schematic from specified connectivity or inspect an existing cellview. Read back the saved circuit to check instances, nets, pins, and device parameters.
- **Run and verify Spectre simulations.** Run operating-point, DC, AC, transient, noise, and single-loop stability analyses, or sweep selected parameters and PVT conditions. Review measured values alongside the executed netlist, logs, and raw PSF data.
- **Continue in ADE.** Run a saved setup, tie results to the exact run history, and pick up the work in Virtuoso for manual review or further analysis.

## Bring your own PDK

Start with a model setup that already runs at your site. Supply the pack with your design and technology libraries, Spectre model files and sections, and the device masters, terminals, and parameter mappings your circuit uses. Set supply, bias, stimulus, and load for the job.

The agent checks those inputs against the saved schematic and Virtuoso’s native netlist. For example, the tested N28 device exposed width as `Wfg`, and the tested 65 nm and 180 nm devices used `w`. That mapping changes with the device. The workflow for readback, simulation, and measurement stays the same. For circuits that depend on extracted views or hierarchy-specific overrides, use your site’s saved ADE setup.

**Live validation.** Design Pack v0.9.0 and public Virtuoso Bridge 1.0.0 ran the same circuit topology through OP, DC sweep, AC, and transient in **TSMC N28, 65 nm, and 180 nm** at one licensed CAD site. All 12 analysis results were checked against the original Spectre PSF data.

## Get the pack

Bring a licensed Virtuoso and Spectre environment, an installed PDK, and an agent with shell and file access. The pack includes six skills, runnable scripts, and configuration templates. Start with a read-only check on a familiar cell before moving on to design changes or larger sweeps.

[View Virtuoso Design Pack on Agensi](https://agensi.io/skills/virtuoso-design-pack)
