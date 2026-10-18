## Problem and resulting behavior

Describe one logical change, its trigger, and the before/after behavior.

## Scope and compatibility

Explain necessary core/startup/USB/build changes. Identify effects on factory /
no-flash, Iceman / flash, command translation, Lua, installation and recovery.
Split independently reviewable work into separate PRs.

## Evidence

List meaningful host tests and CI results. Explain the regression or negative
control. State which physical boundaries were tested and which remain pending;
QEMU does not validate RF, an unavailable accessory or firmware flashing.

## Documentation and generated files

Link the feature documentation under docs/. Explain any generated artifacts that
truly belong in source control; omit build outputs and private lab/card data.
