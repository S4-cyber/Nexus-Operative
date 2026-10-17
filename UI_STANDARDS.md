# Nexus PORTAL control-help standard

Every button, input, dropdown, textarea and other editable control must explain its function and practical effect for a beginner. This is a standing product requirement for existing and future options.

- Static controls carry explicit `data-help` descriptions in `dashboard/portal_preview.html`. Explain what changes, when it takes effect, and relevant limits or costs. Use plain language; distinguish reading data, saving configuration, modeling a trade and making a paid AI request. Do not promise profitability or market execution.
- Create dynamic buttons through `helpButton(description)`. Provide descriptions for any other dynamic input/select/textarea. New controls without meaningful help fail coverage or factory checks; the runtime fallback is a diagnostic fallback, not finished product copy.
- The noninteractive description panel stays at the bottom right, with a solid black background, bright purple text, readable 16 px typography and strong contrast. It must fit small windows and wrap long words. Do not add glow or animation that weakens legibility.
- Show help on pointer hover, including disabled controls. Hide it when the pointer leaves the control, keyboard focus leaves, Escape is pressed, the window loses focus or the control is removed. Nested content inside a button must not cause flicker. Keyboard focus also shows help and connects it with `aria-describedby`, preserving any existing descriptions.
- Hover/focus must never activate controls, send requests, change settings, show input values or expose credentials. Describe a password field’s purpose without reading or echoing its value.
- Keep this reference in source so another development session can continue the same standard.

Validation: run `python -m pytest -q tests/test_control_help.py`, `node tools/control_help_smoke.cjs`, the full regression suite and PORTAL script `node --check`. The tests cover all static controls, the dynamic-button factory, color contrast and actual help-controller event behavior. Browser checks additionally verify placement, hover-off, focus, disabled and dynamic controls at desktop and narrow viewport sizes. Native Windows visual acceptance remains a separate release gate.
