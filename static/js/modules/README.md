PF2E GM Tool — modules drop-in directory.

Any `.js` file you drop here is picked up by `/api/modules` and offered
to the GM in the modules panel. Enable a module to have it loaded on
subsequent map / tracker page renders.

Modules listen to host events via the global `PF2E_HOOKS` registry —
see `static/js/modules.js` for the available event names and the
registry API. A minimal module looks like:

    PF2E_HOOKS.registerModule({ id: 'my-cool-thing', name: 'My Cool Thing', version: '1.0' });
    PF2E_HOOKS.on('token_moved', (t) => console.log('moved:', t.name));

This is a scaffold: there's no manifest, no sandboxing, no signature
check. A module has the same DOM access as the host page, so only
enable code you trust.
