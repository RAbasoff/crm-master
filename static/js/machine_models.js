/* machine_models.js — stylised Three.js models for food-processing machines.
 *
 * One model unit = 1 metre x U (U = 2), matching the existing map_3d.html
 * convention where MACH_SCALE = 0.5 brings it back to metres.
 *
 * Registers window.MachineModels = {
 *   build(d) -> THREE.Group | null,   // d = {style, s, x, z, rot, ...}
 *   styleFor(m) -> styleId,           // m = {name, manufacturer, machine_type}
 *   styles() -> [ids],
 *   footprint(id) -> {w, d, h}        // metres, for layout planning
 * }
 */
(function (global) {
    'use strict';
    var U = 2; // model units per metre

    // ---- shared materials -------------------------------------------------
    function mk(color, o) {
        return new THREE.MeshStandardMaterial(Object.assign({ color: color }, o || {}));
    }
    var MAT = {
        inox: mk(0xd7dde3, { metalness: 0.72, roughness: 0.32 }),
        inoxDark: mk(0xaeb7bf, { metalness: 0.68, roughness: 0.4 }),
        steel: mk(0xb0bec5, { metalness: 0.55, roughness: 0.4 }),
        dark: mk(0x455a64, { metalness: 0.4, roughness: 0.5 }),
        rubber: mk(0x2b2f36, { metalness: 0.25, roughness: 0.75 }),
        white: mk(0xeceff1, { roughness: 0.6 }),
        accent: mk(0x27ae60, { roughness: 0.5 }),
        green: mk(0x2e7d32, { roughness: 0.45, metalness: 0.2 }),
        blue: mk(0x2f7fd0, { metalness: 0.15, roughness: 0.2 }),
        blueBelt: mk(0x2a4fd0, { metalness: 0.1, roughness: 0.6 }),
        glass: mk(0x81d4fa, { transparent: true, opacity: 0.35, roughness: 0.2 }),
        glassBlue: mk(0x2f7fd0, { transparent: true, opacity: 0.42, roughness: 0.18, side: THREE.DoubleSide }),
        yellow: mk(0xf2c31d, { roughness: 0.5, metalness: 0.15 }),
        red: mk(0xc62828, { roughness: 0.45 }),
        screen: mk(0x143d1c, { emissive: 0x1f6b33, emissiveIntensity: 0.4 }),
        pale: mk(0xe8ecef, { metalness: 0.6, roughness: 0.35 })
    };

    // ---- primitive kit (sizes in metres; U converts to model units) -------
    function box(g, w, h, d, x, y, z, m, ry) {
        var mesh = new THREE.Mesh(new THREE.BoxGeometry(w * U, h * U, d * U), m);
        mesh.position.set(x * U, y * U, z * U);
        if (ry) mesh.rotation.y = ry;
        g.add(mesh);
        return mesh;
    }
    function cyl(g, r, h, x, y, z, m, axis) {
        var mesh = new THREE.Mesh(new THREE.CylinderGeometry(r * U, r * U, h * U, 16), m);
        mesh.position.set(x * U, y * U, z * U);
        if (axis === 'x') mesh.rotation.z = Math.PI / 2;
        else if (axis === 'z') mesh.rotation.x = Math.PI / 2;
        g.add(mesh);
        return mesh;
    }
    function cone(g, rTop, rBot, h, x, y, z, m) {
        var mesh = new THREE.Mesh(new THREE.CylinderGeometry(rTop * U, rBot * U, h * U, 20), m);
        mesh.position.set(x * U, y * U, z * U);
        g.add(mesh);
        return mesh;
    }
    function torus(g, r, t, x, y, z, m, ry) {
        var mesh = new THREE.Mesh(new THREE.TorusGeometry(r * U, t * U, 8, 22), m);
        mesh.position.set(x * U, y * U, z * U);
        mesh.rotation.x = Math.PI / 2;
        if (ry) mesh.rotation.y = ry;
        g.add(mesh);
        return mesh;
    }
    /** four legs at the corners of a w x d footprint */
    function legs(g, w, d, h, m, r) {
        var hw = w / 2 - 0.12, hd = d / 2 - 0.12;
        [[-hw, -hd], [hw, -hd], [-hw, hd], [hw, hd]].forEach(function (p) {
            if (r) cyl(g, r, h, p[0], h / 2, p[1], m);
            else box(g, 0.14, h, 0.14, p[0], h / 2, p[1], m);
        });
    }
    function wheels(g, w, d, r, m) {
        var hw = w / 2 - 0.18, hd = d / 2 - 0.14;
        [[-hw, -hd], [hw, -hd], [-hw, hd], [hw, hd]].forEach(function (p) {
            var c = cyl(g, r, 0.11, p[0], r, p[1], m, 'x');
            c.rotation.z = Math.PI / 2;
        });
    }
    /** control box with screen, keypad and emergency stop */
    function panel(g, x, y, z, w, h, m, faceDir) {
        var f = faceDir || 'z';
        box(g, w, h, 0.26, x, y, z, m);
        var dz = (f === 'z') ? 0.14 : 0, dx = (f === 'x') ? 0.14 : 0;
        box(g, w * 0.62, h * 0.42, 0.03, x + dx, y + h * 0.16, z + dz, MAT.dark);
        box(g, w * 0.52, h * 0.32, 0.015, x + dx, y + h * 0.16, z + dz + (f === 'z' ? 0.02 : 0), MAT.glass);
        var by = y - h * 0.22;
        for (var i = 0; i < 3; i++) {
            box(g, 0.075, 0.075, 0.025, x - w * 0.22 + i * 0.11, by, z + dz, MAT.dark);
        }
        // e-stop on a yellow collar
        var esX = x + w * 0.42, esZ = z + (f === 'z' ? 0.16 : 0);
        cyl(g, 0.1, 0.06, esX, y - h * 0.34, esZ, MAT.yellow, 'z');
        cyl(g, 0.072, 0.09, esX, y - h * 0.34, esZ + 0.05, MAT.red, 'z');
    }
    /** horizontal conveyor: frame + belt + side rails + legs */
    function belt(g, len, w, x, y, z, m, railM) {
        box(g, len, 0.1, w, x, y, z, m);
        box(g, len * 0.99, 0.06, w * 0.86, x, y + 0.09, z, MAT.rubber);
        [-1, 1].forEach(function (s) {
            box(g, len, 0.18, 0.05, x, y + 0.16, z + s * w * 0.47, railM || MAT.inox);
        });
        var n = Math.max(2, Math.round(len / 1.2));
        for (var i = 0; i <= n; i++) {
            var lx = x - len / 2 + 0.12 + i * (len - 0.24) / n;
            [-1, 1].forEach(function (s) {
                box(g, 0.09, y, 0.09, lx, y / 2, z + s * w * 0.4, MAT.inoxDark);
            });
        }
    }
    function hopper(g, rTop, rBot, h, x, y, z, m) {
        cone(g, rTop, rBot, h, x, y, z, m);
        torus(g, rTop, 0.05, x, y + h / 2, z, m);
    }

    // ---- registry ---------------------------------------------------------
    var BUILDERS = {};
    function reg(id, fn, fp) {
        BUILDERS[id] = { fn: fn, fp: fp };
    }

    // ============ new machine styles ======================================

    // VEMAG FM250 / MMP 220 hamburger & portion former
    reg('former', function (g) {
        legs(g, 1.5, 1.2, 0.42, MAT.dark, 0.07);
        box(g, 1.5, 1.05, 1.2, 0, 0.95, 0, MAT.inox);
        box(g, 1.56, 0.1, 1.26, 0, 1.5, 0, MAT.inoxDark);
        hopper(g, 0.62, 0.2, 0.72, 0, 1.92, -0.12, MAT.inox);
        // forming head + outfeed plate
        box(g, 0.72, 0.52, 0.62, 0, 1.05, 0.78, MAT.inoxDark);
        box(g, 0.9, 0.05, 0.66, 0, 0.82, 1.22, MAT.inox);
        box(g, 0.9, 0.13, 0.05, 0, 0.89, 1.52, MAT.inox);
        panel(g, -0.86, 1.24, 0.14, 0.62, 0.86, MAT.green, 'x');
        cyl(g, 0.1, 0.16, 0.72, 1.28, 0.42, MAT.inox, 'z');
    }, { w: 1.5, d: 2.2, h: 2.3 });

    // VEMAG VS227 plus sausage linker
    reg('sausage', function (g) {
        legs(g, 1.7, 1.15, 0.44, MAT.dark, 0.08);
        box(g, 1.7, 1.12, 1.15, 0, 1.0, 0, MAT.inox);
        box(g, 1.76, 0.11, 1.21, 0, 1.59, 0, MAT.inoxDark);
        // linking head with handwheel
        box(g, 0.66, 0.72, 0.7, 0, 1.32, 0.86, MAT.inoxDark);
        cyl(g, 0.3, 0.1, 0, 1.45, 1.28, MAT.steel, 'z');
        torus(g, 0.34, 0.045, 0, 1.45, 1.34, MAT.steel);
        // discharge horn
        cone(g, 0.16, 0.26, 0.52, 0, 1.32, 1.62, MAT.inox);
        // film/clipping reel on top
        cyl(g, 0.3, 0.9, 0, 1.98, -0.1, MAT.accent, 'x');
        cyl(g, 0.07, 1.0, 0, 1.98, -0.1, MAT.inoxDark, 'x');
        panel(g, -0.98, 1.3, 0.06, 0.6, 0.82, MAT.green, 'x');
    }, { w: 1.7, d: 2.6, h: 2.2 });

    // VEMAG TM 203 guillotine cutoff
    reg('cutoff', function (g) {
        legs(g, 1.1, 1.0, 0.5, MAT.dark, 0.07);
        box(g, 1.1, 1.05, 1.0, 0, 1.02, 0, MAT.inox);
        // guillotine frame + blade
        box(g, 0.14, 1.15, 0.9, -0.52, 1.85, 0, MAT.inoxDark);
        box(g, 0.14, 1.15, 0.9, 0.52, 1.85, 0, MAT.inoxDark);
        box(g, 1.18, 0.16, 0.9, 0, 2.42, 0, MAT.inoxDark);
        box(g, 0.78, 0.07, 0.72, 0, 1.98, 0, MAT.steel);
        // product track through the blade
        box(g, 1.9, 0.1, 0.5, 0, 1.22, 0, MAT.inox);
        [-1, 1].forEach(function (s) {
            box(g, 1.9, 0.16, 0.05, 0, 1.35, s * 0.27, MAT.inox);
        });
        panel(g, 0.72, 1.6, 0.42, 0.44, 0.62, MAT.dark, 'x');
    }, { w: 1.9, d: 1.1, h: 2.5 });

    // Klipsmachine — Poly-clip class clipper
    reg('clipper', function (g) {
        legs(g, 1.35, 1.15, 0.42, MAT.dark, 0.07);
        box(g, 1.35, 1.18, 1.15, 0, 1.0, 0, MAT.inox);
        box(g, 1.41, 0.1, 1.21, 0, 1.62, 0, MAT.inoxDark);
        // clipping head under a blue hood
        box(g, 0.86, 0.62, 0.82, 0, 1.9, 0.1, MAT.glassBlue);
        box(g, 0.92, 0.08, 0.88, 0, 2.23, 0.1, MAT.inoxDark);
        cyl(g, 0.16, 0.56, 0, 1.9, 0.1, MAT.steel);
        // infeed funnel and outfeed
        cone(g, 0.3, 0.16, 0.46, 0, 1.72, -0.62, MAT.inox);
        box(g, 1.15, 0.08, 0.52, 0, 1.18, 1.02, MAT.inox);
        panel(g, 0.82, 1.42, 0.36, 0.52, 0.76, MAT.green, 'x');
    }, { w: 1.4, d: 2.4, h: 2.3 });

    // TREIF Argon / Argon+ cube cutter
    reg('dicer', function (g) {
        legs(g, 1.6, 1.35, 0.46, MAT.dark, 0.08);
        box(g, 1.6, 1.16, 1.35, 0, 1.02, 0, MAT.pale);
        box(g, 1.66, 0.11, 1.41, 0, 1.63, 0, MAT.inoxDark);
        // loading table + product pusher
        box(g, 1.5, 0.09, 1.0, 0, 1.24, -1.15, MAT.inox);
        [-1, 1].forEach(function (s) {
            box(g, 1.5, 0.2, 0.05, 0, 1.39, -1.15 + s * 0.5, MAT.inox);
        });
        box(g, 0.34, 0.66, 0.9, 0, 1.6, -0.78, MAT.inoxDark);
        // cutting box + hood
        box(g, 1.18, 0.72, 1.1, 0, 2.1, 0.12, MAT.pale);
        box(g, 1.24, 0.09, 1.16, 0, 2.5, 0.12, MAT.inoxDark);
        // discharge chute
        box(g, 0.7, 0.08, 0.78, 0, 1.36, 1.28, MAT.inox);
        box(g, 0.7, 0.17, 0.05, 0, 1.48, 1.64, MAT.inox);
        panel(g, -0.94, 1.66, 0.42, 0.58, 0.82, MAT.green, 'x');
    }, { w: 1.6, d: 3.4, h: 2.6 });

    // MHS PCE70/21KS slicer / Nock MBC 330 breaking machine
    reg('slicer', function (g) {
        legs(g, 1.35, 1.05, 0.44, MAT.dark, 0.07);
        box(g, 1.35, 1.12, 1.05, 0, 1.0, 0, MAT.pale);
        box(g, 1.41, 0.1, 1.11, 0, 1.59, 0, MAT.inoxDark);
        // cutting chamber with transparent guard
        box(g, 1.1, 0.78, 0.9, 0, 1.94, 0.06, MAT.glassBlue);
        box(g, 1.16, 0.08, 0.96, 0, 2.36, 0.06, MAT.inoxDark);
        // circular blade
        cyl(g, 0.4, 0.045, 0, 1.98, 0.06, MAT.steel, 'z');
        // infeed tray and outfeed belt
        box(g, 1.05, 0.08, 0.72, 0, 1.3, -0.92, MAT.inox);
        box(g, 1.2, 0.09, 0.62, 0, 1.12, 0.98, MAT.inox);
        box(g, 1.2, 0.055, 0.54, 0, 1.19, 0.98, MAT.rubber);
        panel(g, -0.8, 1.5, 0.36, 0.54, 0.78, MAT.green, 'x');
    }, { w: 1.35, d: 2.6, h: 2.4 });

    // F.P.M. ST 700 band saw
    reg('saw', function (g) {
        var w = 1.15, d = 1.25;
        legs(g, w, d, 0.52, MAT.dark, 0.09);
        box(g, w, 0.28, d, 0, 0.66, 0, MAT.pale);           // base cabinet
        box(g, w * 0.98, 0.08, d * 0.98, 0, 0.84, 0, MAT.inoxDark);
        // two vertical columns and the saw housing
        [-1, 1].forEach(function (s) {
            box(g, 0.17, 1.55, 0.17, s * (w / 2 - 0.12), 1.7, 0, MAT.pale);
        });
        box(g, w * 0.98, 0.5, d * 0.62, 0, 2.28, -0.12, MAT.pale);   // upper housing
        box(g, w * 0.98, 0.1, d * 0.66, 0, 2.55, -0.12, MAT.inoxDark);
        // work table with blade slot
        box(g, w * 0.92, 0.09, d * 0.9, 0, 1.24, 0.06, MAT.inox);
        box(g, 0.04, 0.36, 0.5, 0, 1.4, 0.06, MAT.dark);      // blade slot guide
        // blade wheels hinted as discs behind the housing
        cyl(g, 0.31, 0.06, 0, 1.75, -0.34, MAT.dark, 'z');
        cyl(g, 0.31, 0.06, 0, 2.16, -0.34, MAT.dark, 'z');
        // blade handle / guard
        box(g, 0.12, 0.72, 0.1, w / 2 - 0.2, 1.72, 0.44, MAT.inoxDark);
        panel(g, -0.68, 1.62, 0.3, 0.42, 0.6, MAT.dark, 'x');
    }, { w: 1.15, d: 1.25, h: 2.6 });

    // Boldt industrial meat mincer
    reg('grinder', function (g) {
        legs(g, 1.25, 1.05, 0.5, MAT.dark, 0.08);
        box(g, 1.25, 0.95, 1.05, 0, 0.98, 0, MAT.inox);
        box(g, 1.31, 0.1, 1.11, 0, 1.48, 0, MAT.inoxDark);
        // feed tray + worm housing + mincing head
        box(g, 1.15, 0.1, 0.8, 0, 1.36, -0.62, MAT.inox);
        box(g, 1.15, 0.22, 0.05, 0, 1.5, -1.0, MAT.inox);
        cyl(g, 0.31, 0.92, 0, 1.52, 0.16, MAT.inoxDark, 'z');
        cyl(g, 0.37, 0.16, 0, 1.52, 0.66, MAT.steel, 'z');
        cyl(g, 0.29, 0.06, 0, 1.52, 0.78, MAT.dark, 'z');    // perforated plate
        torus(g, 0.37, 0.05, 0, 1.52, 0.7, MAT.inox);
        // motor block behind
        box(g, 0.78, 0.72, 0.72, 0, 1.5, -0.62, MAT.inoxDark);
        panel(g, -0.78, 1.34, 0.24, 0.46, 0.64, MAT.green, 'x');
    }, { w: 1.25, d: 1.9, h: 2.0 });

    // Günther vacuum tumbler — horizontal drum on a frame
    reg('tumbler', function (g) {
        var R = 0.78, L = 1.9;
        // frame
        [-1, 1].forEach(function (s) {
            box(g, 0.22, 1.15, 0.22, s * 0.82, 0.58, -0.62, MAT.pale);
            box(g, 0.22, 1.15, 0.22, s * 0.82, 0.58, 0.62, MAT.pale);
        });
        box(g, 1.95, 0.18, 1.62, 0, 1.14, 0, MAT.inoxDark);
        wheels(g, 1.95, 1.62, 0.18, MAT.rubber);
        // drum
        var drum = cyl(g, R, L, 0, 1.82, 0, MAT.inox, 'z');
        drum.rotation.x = Math.PI / 2;
        torus(g, R + 0.03, 0.055, 0, 1.82, -L / 2 + 0.16, MAT.inoxDark);
        torus(g, R + 0.03, 0.055, 0, 1.82, L / 2 - 0.16, MAT.inoxDark);
        // loading hatch on the drum
        box(g, 0.82, 0.1, 0.72, 0, 2.55, 0, MAT.inoxDark);
        // vacuum pump + motor behind
        cyl(g, 0.26, 0.62, 0, 1.62, -1.28, MAT.dark, 'z');
        box(g, 0.62, 0.52, 0.56, 0.72, 1.62, -1.28, MAT.inoxDark);
        panel(g, -1.02, 1.62, 0.82, 0.56, 0.82, MAT.green, 'x');
    }, { w: 2.0, d: 3.2, h: 2.7 });

    // Rühle MPR 220 mixer / tumbler / marinator
    reg('mixer', function (g) {
        legs(g, 1.3, 1.25, 0.46, MAT.dark, 0.08);
        box(g, 1.3, 1.02, 1.25, 0, 0.95, 0, MAT.inox);
        box(g, 1.36, 0.1, 1.31, 0, 1.49, 0, MAT.inoxDark);
        // tilted mixing bowl
        var bowl = cone(g, 0.72, 0.34, 0.78, 0, 1.92, 0, MAT.inox);
        bowl.rotation.x = -0.34;
        torus(g, 0.72, 0.05, 0, 2.28, -0.13, MAT.inoxDark);
        // lid
        var lid = cyl(g, 0.74, 0.07, 0, 2.36, -0.16, MAT.glass);
        lid.rotation.x = -0.34;
        // vacuum pump block + discharge
        box(g, 0.52, 0.46, 0.5, -0.78, 1.68, -0.5, MAT.inoxDark);
        cyl(g, 0.13, 0.5, 0.62, 1.28, 0.34, MAT.inox, 'x');
        panel(g, 0.78, 1.4, 0.42, 0.5, 0.72, MAT.green, 'x');
    }, { w: 1.3, d: 1.5, h: 2.5 });

    // hydraulic scissor lift (Günther / Seydelmann bowl-cutter lift)
    reg('lift', function (g) {
        box(g, 1.15, 0.16, 1.05, 0, 0.14, 0, MAT.inoxDark);   // base plate
        wheels(g, 1.15, 1.05, 0.16, MAT.rubber);
        // scissor arms
        [-1, 1].forEach(function (s) {
            var arm = box(g, 0.13, 1.25, 0.13, s * 0.32, 0.72, 0, MAT.pale);
            arm.rotation.z = s * 0.42;
        });
        cyl(g, 0.09, 0.9, 0, 0.72, 0, MAT.inoxDark, 'x');     // pivot pin
        // hydraulic ram
        var ram = cyl(g, 0.08, 0.82, 0, 0.66, 0.22, MAT.steel);
        ram.rotation.z = 0.55;
        // top platform with a lip
        box(g, 1.12, 0.11, 1.02, 0, 1.36, 0, MAT.inox);
        [-1, 1].forEach(function (s) {
            box(g, 1.12, 0.16, 0.05, 0, 1.47, s * 0.5, MAT.inox);
        });
        // push handle
        box(g, 0.1, 0.1, 0.86, -0.62, 1.42, 0, MAT.inoxDark);
        box(g, 0.1, 0.72, 0.1, -0.62, 1.78, -0.38, MAT.inoxDark);
        box(g, 0.1, 0.72, 0.1, -0.62, 1.78, 0.38, MAT.inoxDark);
    }, { w: 1.15, d: 1.1, h: 1.6 });

    // vacuum packer: Cretel VA 620 double chamber / Henkelman Polar 80
    reg('vacuum', function (g) {
        var dbl = g.userData && g.userData.variant === 'double';
        var W = dbl ? 2.0 : 1.15, D = dbl ? 1.15 : 0.95, H = 1.02;
        legs(g, W, D, 0.42, MAT.dark, 0.08);
        box(g, W, H, D, 0, 0.42 + H / 2, 0, MAT.inox);
        box(g, W + 0.06, 0.1, D + 0.06, 0, 0.42 + H + 0.05, 0, MAT.inoxDark);
        // chamber wells
        var wells = dbl ? [-W / 4, W / 4] : [0];
        wells.forEach(function (x) {
            box(g, W * (dbl ? 0.42 : 0.78), 0.06, D * 0.72, x, 0.42 + H + 0.11, 0, MAT.dark);
            box(g, W * (dbl ? 0.4 : 0.74), 0.2, D * 0.68, x, 0.42 + H - 0.02, 0, MAT.inoxDark);
        });
        // hinged lid (flat stainless) shown raised
        var lid = box(g, W * 0.96, 0.09, D * 0.92, 0, 0.42 + H + 0.62, -D * 0.42, MAT.inox);
        lid.rotation.x = -1.05;
        box(g, W * 0.96, 0.05, D * 0.12, 0, 0.42 + H + 0.32, -D * 0.52, MAT.glass);
        panel(g, W / 2 - 0.02, 0.42 + H * 0.72, D / 2 + 0.02, dbl ? 0.72 : 0.56, 0.66, MAT.green, 'x');
    }, { w: 2.0, d: 1.15, h: 2.1 });

    // ProSeal GT-class tray sealer
    reg('trayseal', function (g) {
        legs(g, 1.55, 1.35, 0.46, MAT.dark, 0.08);
        box(g, 1.55, 1.12, 1.35, 0, 1.02, 0, MAT.inox);
        box(g, 1.61, 0.1, 1.41, 0, 1.61, 0, MAT.inoxDark);
        // sealing head / hood
        box(g, 1.24, 0.72, 1.12, 0, 2.02, 0, MAT.inox);
        box(g, 1.3, 0.1, 1.18, 0, 2.42, 0, MAT.inoxDark);
        box(g, 1.02, 0.34, 0.02, 0, 2.02, 0.58, MAT.glass);
        // film reel over the head
        cyl(g, 0.28, 1.05, 0, 2.72, -0.3, MAT.accent, 'x');
        cyl(g, 0.07, 1.15, 0, 2.72, -0.3, MAT.inoxDark, 'x');
        // infeed and outfeed conveyors
        belt(g, 1.35, 0.72, 0, 1.12, -1.36, MAT.inox);
        belt(g, 1.35, 0.72, 0, 1.12, 1.36, MAT.inox);
        panel(g, 0.92, 1.58, 0.42, 0.56, 0.8, MAT.green, 'x');
    }, { w: 1.6, d: 4.0, h: 2.9 });

    // ILPRA PENTA 2100 / Pentavac VFFS bagger
    reg('vffs', function (g) {
        box(g, 1.25, 1.55, 1.15, 0, 0.82, 0, MAT.pale);
        box(g, 1.31, 0.11, 1.21, 0, 1.62, 0, MAT.inoxDark);
        wheels(g, 1.25, 1.15, 0.15, MAT.rubber);
        // forming tube + shoulder
        cone(g, 0.42, 0.3, 0.62, 0, 1.96, 0, MAT.inox);
        cyl(g, 0.3, 0.58, 0, 2.42, 0, MAT.inox);
        torus(g, 0.3, 0.05, 0, 2.7, 0, MAT.inoxDark);
        // film reel on a back stand
        box(g, 0.14, 1.5, 0.14, 0, 2.2, -0.86, MAT.inoxDark);
        box(g, 0.14, 1.5, 0.14, 0, 2.2, -1.16, MAT.inoxDark);
        cyl(g, 0.42, 1.05, 0, 2.82, -1.01, MAT.glass, 'x');
        cyl(g, 0.08, 1.16, 0, 2.82, -1.01, MAT.inoxDark, 'x');
        // sealing jaws
        box(g, 0.86, 0.26, 0.34, 0, 1.42, 0, MAT.inoxDark);
        box(g, 0.86, 0.2, 0.3, 0, 1.16, 0, MAT.inoxDark);
        // discharge
        box(g, 0.62, 0.07, 0.62, 0, 0.86, 0.62, MAT.inox);
        panel(g, 0.76, 1.5, 0.36, 0.5, 0.76, MAT.green, 'x');
    }, { w: 1.3, d: 2.4, h: 3.0 });

    // VWS Octopus MAX print & apply labeler
    reg('labeler', function (g) {
        // post + arm over the line
        box(g, 0.2, 2.05, 0.2, 0, 1.03, 0, MAT.pale);
        box(g, 1.15, 0.16, 0.2, 0.52, 2.02, 0, MAT.pale);
        // label reel
        cyl(g, 0.36, 0.22, 0.98, 2.24, 0, MAT.white, 'x');
        cyl(g, 0.09, 0.28, 0.98, 2.24, 0, MAT.inoxDark, 'x');
        // applicator head
        box(g, 0.34, 0.52, 0.42, 1.06, 1.72, 0, MAT.pale);
        box(g, 0.26, 0.1, 0.34, 1.06, 1.42, 0, MAT.rubber);
        // small control box
        box(g, 0.42, 0.56, 0.24, -0.12, 1.62, 0, MAT.dark);
        box(g, 0.3, 0.2, 0.02, -0.12, 1.74, 0.13, MAT.glass);
        cyl(g, 0.07, 0.06, -0.12, 1.42, 0.13, MAT.red, 'z');
        // base plate
        box(g, 0.62, 0.1, 0.62, 0, 0.05, 0, MAT.inoxDark);
    }, { w: 1.3, d: 0.7, h: 2.3 });

    // LOMA MD304 / IQ4 metal detector over a conveyor
    reg('detector', function (g) {
        // conveyor through the aperture
        belt(g, 2.5, 0.62, 0, 0.92, 0, MAT.inox);
        // detector portal
        var W = 1.25, H = 0.78, T = 0.62;
        box(g, T, 0.2, W, 0, 0.92 + H + 0.12, 0, MAT.pale);           // top beam
        [-1, 1].forEach(function (s) {
            box(g, T, H + 0.24, 0.2, 0, 0.92 + (H + 0.24) / 2, s * (W / 2 - 0.1), MAT.pale);
        });
        box(g, T + 0.04, 0.16, W + 0.06, 0, 0.92 + 0.06, 0, MAT.inoxDark); // lower beam
        // control head on the side
        box(g, 0.5, 0.62, 0.36, 0, 1.62, W / 2 + 0.12, MAT.pale);
        box(g, 0.34, 0.26, 0.02, 0, 1.72, W / 2 + 0.31, MAT.glass);
        for (var i = 0; i < 3; i++) {
            box(g, 0.07, 0.07, 0.025, -0.12 + i * 0.1, 1.5, W / 2 + 0.31, MAT.dark);
        }
        cyl(g, 0.075, 0.05, 0.16, 1.44, W / 2 + 0.31, MAT.red, 'z');
        // brand strip
        box(g, 0.02, 0.16, W * 0.6, T / 2 + 0.01, 0.92 + H + 0.12, 0, MAT.accent);
    }, { w: 1.4, d: 2.6, h: 1.9 });

    // RATIONAL iCombiPro combi-steamer (6-1/1 and 10-1/1)
    reg('oven', function (g) {
        var big = g.userData && g.userData.variant === 'big';
        var W = big ? 0.87 : 0.87, D = big ? 0.84 : 0.84, H = big ? 1.04 : 0.84;
        var y0 = 0.06;
        // cabinet
        box(g, W, H, D, 0, y0 + H / 2, 0, MAT.inox);
        box(g, W + 0.03, 0.07, D + 0.03, 0, y0 + H + 0.035, 0, MAT.inoxDark);
        // feet
        [[-1, -1], [1, -1], [-1, 1], [1, 1]].forEach(function (p) {
            cyl(g, 0.045, 0.06, p[0] * (W / 2 - 0.09), 0.03, p[1] * (D / 2 - 0.09), MAT.dark);
        });
        // glass door with a handle
        box(g, W * 0.82, H * 0.62, 0.03, -0.04, y0 + H * 0.5, D / 2 + 0.015, MAT.glass);
        box(g, W * 0.86, H * 0.66, 0.025, -0.04, y0 + H * 0.5, D / 2, MAT.dark);
        box(g, 0.05, H * 0.5, 0.07, W / 2 - 0.06, y0 + H * 0.5, D / 2 + 0.06, MAT.inoxDark);
        // control panel on the right column
        box(g, W * 0.2, H * 0.78, 0.035, W / 2 - 0.11, y0 + H * 0.52, D / 2 + 0.015, MAT.dark);
        box(g, W * 0.15, H * 0.3, 0.012, W / 2 - 0.11, y0 + H * 0.66, D / 2 + 0.035, MAT.glass);
        for (var r = 0; r < 3; r++) {
            for (var c = 0; c < 3; c++) {
                box(g, 0.035, 0.035, 0.012, W / 2 - 0.15 + c * 0.045, y0 + H * 0.42 - r * 0.045, D / 2 + 0.035, MAT.inoxDark);
            }
        }
        cyl(g, 0.05, 0.035, W / 2 - 0.11, y0 + H * 0.22, D / 2 + 0.035, MAT.red, 'z');
        // rack trolley handle hint on top
        box(g, W * 0.86, 0.05, 0.05, 0, y0 + H + 0.1, -D / 2 + 0.06, MAT.inoxDark);
    }, { w: 0.9, d: 0.85, h: 1.0 });

    // CES IQF freezer tunnel
    reg('freezer', function (g) {
        var L = 4.0, W = 3.0, H = 2.2;
        box(g, L, H, W, 0, H / 2 + 0.12, 0, MAT.pale);
        box(g, L + 0.08, 0.14, W + 0.08, 0, H + 0.19, 0, MAT.inoxDark);
        box(g, L + 0.08, 0.14, W + 0.08, 0, 0.07, 0, MAT.inoxDark);
        // insulation panel seams
        for (var i = -1; i <= 1; i++) {
            box(g, 0.035, H - 0.16, 0.035, i * L / 3, H / 2 + 0.12, W / 2 + 0.01, MAT.inoxDark);
            box(g, 0.035, H - 0.16, 0.035, i * L / 3, H / 2 + 0.12, -W / 2 - 0.01, MAT.inoxDark);
        }
        // infeed / outfeed conveyors through the ends
        belt(g, 1.7, 1.15, -L / 2 - 0.85, 1.02, 0, MAT.inox);
        belt(g, 1.7, 1.15, L / 2 + 0.85, 1.02, 0, MAT.inox);
        // access door + control panel on the long side
        box(g, 1.15, 1.85, 0.08, -L / 4, 1.05, W / 2 + 0.05, MAT.inoxDark);
        box(g, 1.05, 1.72, 0.03, -L / 4, 1.05, W / 2 + 0.1, MAT.glass);
        box(g, 0.07, 0.62, 0.07, -L / 4 + 0.5, 1.15, W / 2 + 0.14, MAT.inox);
        panel(g, L / 4, 1.5, W / 2 + 0.08, 0.95, 1.15, MAT.dark, 'z');
    }, { w: 4.0, d: 3.0, h: 2.4 });

    // DYNACO D-313 high-speed roll-up door
    reg('door', function (g) {
        var W = 2.6, H = 3.2;
        [-1, 1].forEach(function (s) {
            box(g, 0.34, H, 0.32, s * W / 2, H / 2, 0, MAT.pale);
        });
        box(g, W + 0.68, 0.72, 0.55, 0, H + 0.36, 0, MAT.pale);   // roll housing
        cyl(g, 0.32, W + 0.3, 0, H + 0.34, 0, MAT.dark, 'x');     // rolled curtain
        // half-lifted curtain
        box(g, W - 0.1, 1.25, 0.07, 0, H - 0.62, 0, MAT.accent);
        box(g, W - 0.1, 0.12, 0.1, 0, H - 1.24, 0, MAT.yellow);
        // side guides + control box
        [-1, 1].forEach(function (s) {
            box(g, 0.1, H, 0.12, s * (W / 2 - 0.06), H / 2, 0, MAT.inoxDark);
        });
        box(g, 0.42, 0.62, 0.3, W / 2 + 0.42, 1.35, 0.18, MAT.dark);
        box(g, 0.28, 0.2, 0.02, W / 2 + 0.42, 1.48, 0.34, MAT.glass);
        cyl(g, 0.075, 0.05, W / 2 + 0.42, 1.16, 0.34, MAT.red, 'z');
    }, { w: 3.3, d: 0.6, h: 3.7 });

    // Verbufa VSP-1 brine injector / tenderiser
    reg('injector', function (g) {
        legs(g, 1.35, 1.2, 0.46, MAT.dark, 0.08);
        box(g, 1.35, 1.05, 1.2, 0, 0.98, 0, MAT.inox);
        box(g, 1.41, 0.1, 1.26, 0, 1.53, 0, MAT.inoxDark);
        // needle beam over the belt
        belt(g, 1.5, 0.72, 0, 1.12, 0, MAT.inox);
        box(g, 1.3, 0.42, 0.66, 0, 1.98, 0, MAT.inoxDark);
        box(g, 1.34, 0.07, 0.7, 0, 2.22, 0, MAT.inox);
        for (var i = -3; i <= 3; i++) {
            cyl(g, 0.035, 0.42, i * 0.17, 1.72, 0, MAT.steel);
        }
        // brine tank + pump
        box(g, 0.62, 0.62, 0.62, -1.02, 0.82, 0, MAT.inox);
        cyl(g, 0.14, 0.32, -1.02, 1.3, 0, MAT.inoxDark);
        cyl(g, 0.06, 0.7, -0.6, 1.55, 0, MAT.inox, 'x');
        panel(g, 0.82, 1.55, 0.42, 0.52, 0.78, MAT.green, 'x');
    }, { w: 1.35, d: 2.6, h: 2.3 });

    // steam-heated rack ovens (Handtmann 'Bakken', offline)
    reg('ovenbak', function (g) {
        box(g, 1.35, 1.95, 1.15, 0, 1.02, 0, MAT.inox);
        box(g, 1.41, 0.11, 1.21, 0, 2.03, 0, MAT.inoxDark);
        wheels(g, 1.35, 1.15, 0.14, MAT.rubber);
        // double doors with windows and handles
        [-1, 1].forEach(function (s) {
            box(g, 0.62, 1.55, 0.06, s * 0.33, 1.08, 0.59, MAT.inoxDark);
            box(g, 0.46, 0.92, 0.02, s * 0.33, 1.22, 0.63, MAT.glass);
            box(g, 0.05, 0.5, 0.06, s * 0.06, 1.08, 0.68, MAT.inox);
        });
        // steam manifold on top
        cyl(g, 0.11, 1.15, 0, 2.22, 0, MAT.inoxDark, 'x');
        [-1, 1].forEach(function (s) {
            cyl(g, 0.07, 0.36, s * 0.42, 2.42, 0, MAT.steel);
        });
        box(g, 0.46, 0.56, 0.24, 0.78, 1.62, 0.52, MAT.dark);
        box(g, 0.3, 0.2, 0.02, 0.78, 1.72, 0.65, MAT.glass);
    }, { w: 1.35, d: 1.15, h: 2.5 });

    // generic stainless control cabinet — the fallback body
    reg('cabinet', function (g) {
        box(g, 1.3, 1.1, 0.9, 0, 0.55 + 0.42, 0, MAT.inox);
        box(g, 1.36, 0.1, 0.96, 0, 1.52, 0, MAT.inoxDark);
        legs(g, 1.3, 0.9, 0.42, MAT.dark, 0.06);
        box(g, 0.55, 0.42, 0.05, 0, 1.18, 0.46, MAT.dark);
        box(g, 0.42, 0.26, 0.02, 0, 1.22, 0.5, MAT.glass);
        box(g, 1.32, 0.12, 0.92, 0, 0.62, 0, MAT.accent);
    }, { w: 1.3, d: 0.95, h: 1.6 });

    // Bandall BA32 banding machine — band reel over a product arch
    reg('bander', function (g) {
        box(g, 1.15, 0.72, 0.82, 0, 0.36 + 0.06, 0, MAT.pale);
        box(g, 1.21, 0.09, 0.88, 0, 0.78, 0, MAT.inoxDark);
        wheels(g, 1.15, 0.82, 0.13, MAT.rubber);
        // arch the product passes under
        [-1, 1].forEach(function (s) {
            box(g, 0.12, 0.95, 0.12, s * 0.36, 1.28, 0, MAT.inox);
        });
        box(g, 0.84, 0.12, 0.12, 0, 1.76, 0, MAT.inox);
        // band reel + feed arm
        cyl(g, 0.3, 0.16, 0, 2.12, -0.16, MAT.white, 'x');
        cyl(g, 0.08, 0.22, 0, 2.12, -0.16, MAT.inoxDark, 'x');
        box(g, 0.1, 0.42, 0.1, 0, 1.94, -0.02, MAT.inoxDark);
        // sealing head + touch panel
        box(g, 0.34, 0.34, 0.3, 0, 1.28, 0.28, MAT.inoxDark);
        box(g, 0.5, 0.62, 0.22, 0.62, 1.16, 0.34, MAT.dark);
        box(g, 0.36, 0.3, 0.02, 0.62, 1.28, 0.46, MAT.glass);
        cyl(g, 0.07, 0.05, 0.62, 0.96, 0.46, MAT.red, 'z');
    }, { w: 1.2, d: 0.9, h: 2.3 });

    // ---- style resolution + air demand ------------------------------------
    // Researched per-model figures (docs/СПРАВОЧНИК_ПОТРЕБЛЕНИЯ_СТАНКОВ.md).
    // Only the consumers are listed; everything else is 0.
    // NOTE: `norm()` collapses punctuation to single spaces, so patterns must
    // match normalised text ("CAVECO - 2" -> "caveco 2").
    var AIR = [
        [/(caveco [2345]|caveco skin|skin machine|^ypsilon|ypsilon$)/, 45],
        [/(magic|caveco 1)/, 30],
        [/(penta)/, 18],
        [/(meat slicer|nock|mbc)/, 6],
        [/(ishida|weger)/, 6],
        [/(verbufa|spie)/, 4],
        [/(oktopus|octopus)/, 3.6],
        [/(pro ?seal)/, 3],
        [/(vulbus|hp30|vemag hp)/, 2.4],
        [/(klips)/, 2.4],
        [/(loma|metaaldetector|md304|q4w45|iq4)/, 2.5],
        [/(guilloit|guillot)/, 2],
        [/(bandall|bander)/, 0.9],
        [/(snelport|dynaco)/, 0.6],
        [/(vs227|worstmachine)/, 0.5],
        [/(fm250|hamburger vorm)/, 0.5]
    ];

    function norm(s) {
        return (s || '').toString().toLowerCase()
            .replace(/u0308/g, '')
            .replace(/[üûù]/g, 'u')
            .replace(/[éèêë]/g, 'e')
            .replace(/[äâà]/g, 'a')
            .replace(/[öô]/g, 'o')
            .replace(/[ïî]/g, 'i')
            .replace(/[^a-z0-9]+/g, ' ')
            .trim();
    }

    function airDemand(m) {
        m = m || {};
        var hay = norm([m.name, m.manufacturer, m.machine_type || m.type, m.model].join(' '));
        for (var i = 0; i < AIR.length; i++) {
            if (AIR[i][0].test(hay)) return AIR[i][1];
        }
        return 0;
    }

    // order matters: most specific patterns first
    var RULES = [
        [/(lift)/, 'lift'],                                  // 'Lift Gunter' before gunther/tumbler
        [/(gt1|tray seal|proseal|pro seal)/, 'trayseal'],
        [/(bandall|bander|banding|bundel)/, 'bander'],
        [/(penta ?vac|penta 2100|vffs|ulma|vertic|bagger)/, 'vffs'],
        [/(trommel|tumbler|gunther|ghunter|gunter trommel)/, 'tumbler'],
        [/(menger|mpr |rühle|rhule|ruhle|mixer|marinat)/, 'mixer'],
        [/(cretel|henkel|henkelman|polar|vacuum|vacuummachine|vacuum machine|vacu m|dubbelkamer|double chamber)/, 'vacuum'],
        [/(oktopus|octopus|label|etiket|vws)/, 'labeler'],
        [/(metaaldetector|metal detect|md304|iq4|iq3|q4w45|loma)/, 'detector'],
        [/(icombi|combi ?pro|rational|combi steamer)/, 'oven'],
        [/(vriestun|freezer|iqf|ces |ces-|vries)/, 'freezer'],
        [/(snelport|dynaco|d 313|roldeur|high speed door|sectional)/, 'door'],
        [/(verbufa|vsp|inject|pekel|brine|spi[eë])/, 'injector'],
        [/(tm 203|guilloit|guillot|cutoff|cut off|cutting machine|snijblok)/, 'cutoff'],
        [/(vs227|worstmachine|sausage|rookworst|linker)/, 'sausage'],
        [/(fm250|gehaakt|mmp 220|hamburger|former|portie)/, 'former'],
        [/(klips|clipper|poly clip|clip)/, 'clipper'],
        [/(treif|argon|cube cut|dicer|blokjes)/, 'dicer'],
        [/(mhs|pce70|pce 70|slicer|portion cut|nock|mbc|meat slicer|vlees snij)/, 'slicer'],
        [/(steen|st 700|band ?saw|zaag|bandschijf)/, 'saw'],
        [/(boldt|vlees molen|molen|grinder|mincer)/, 'grinder'],
        [/(bakken|stoomoven|steam oven|oven [0-9]|oven$)/, 'ovenbak'],
        [/(vulbus|hp30|hp 30|filler|vulmachine|worst snij|worstsnij)/, 'filler'],
        [/(ypsilon|caveco|magic|skin machine|vacuum skin|inpak)/, 'ypsilon'],
        [/(ishida|weger|multihead|weeg)/, 'ishida'],
        [/(conveyor|transportband|band |incline|opvoer|elevator|bucket)/, 'conveyor'],
        [/(compressor|schroefcompressor)/, 'compressor']
    ];

    // Brands are resolved from the NAME alone first: CRM «type» fields are
    // user-entered and often describe an accessory instead of the machine
    // (ISHIDA carries the type «Lift, vlees verdelen»).
    var BRAND = [
        [/(lift)/, 'lift'],
        [/(ishida|weger)/, 'ishida'],
        [/(treif|argon)/, 'dicer'],
        [/(caveco|ypsilon|magic|skin machine)/, 'ypsilon'],
        [/(vulbus|vemag hp|hp30)/, 'filler'],
        [/(rational|icombi)/, 'oven'],
        [/(loma|metaaldetector|md304|q4w45)/, 'detector'],
        [/(mhs|nock|mbc|slicer|portion cut)/, 'slicer'],
        [/(boldt)/, 'grinder'],
        [/(steen|st 700)/, 'saw'],
        [/(oktopus|octopus)/, 'labeler'],
        [/(pro ?seal)/, 'trayseal'],
        [/(penta)/, 'vffs'],
        [/(bandall)/, 'bander'],
        [/(henkelman|cretel)/, 'vacuum'],
        [/(ghunter|gunther|trommel|tumbler)/, 'tumbler'],
        [/(rhule|ruhle|rühle|menger)/, 'mixer'],
        [/(verbufa|spi[eë])/, 'injector'],
        [/(dynaco|snelport)/, 'door'],
        [/(klips)/, 'clipper'],
        [/(guilloit|guillot)/, 'cutoff'],
        [/(gehaakt|mmp|hamburger|fm250)/, 'former'],
        [/(worstmachine|vs227)/, 'sausage'],
        [/(vriestun|ces|freezer|iqf)/, 'freezer'],
        [/(bakken|stoomoven)/, 'ovenbak']
    ];

    function styleFor(m) {
        m = m || {};
        var nm = norm(m.name), i;
        for (i = 0; i < BRAND.length; i++) {
            if (BRAND[i][0].test(nm)) return BRAND[i][1];
        }
        // затем точные поля (имя / производитель / тип / модель),
        // свободное описание — только последним проходом: оно часто содержит
        // лишние слова («Vlees molen») и даёт ложные совпадения
        var core = norm([m.name, m.manufacturer, m.machine_type || m.type, m.model].join(' '));
        for (i = 0; i < RULES.length; i++) {
            if (RULES[i][0].test(core)) return RULES[i][1];
        }
        var loose = norm([core, m.description].join(' '));
        for (i = 0; i < RULES.length; i++) {
            if (RULES[i][0].test(loose)) return RULES[i][1];
        }
        return 'cabinet';
    }

    function footprint(id) {
        var b = BUILDERS[id];
        return (b && b.fp) || { w: 1.4, d: 1.2, h: 1.6 };
    }

    function build(d) {
        var b = BUILDERS[d.style];
        if (!b) return null;
        var g = new THREE.Group();
        if (d.variant) g.userData.variant = d.variant;
        b.fn(g, d);
        return g;
    }

    global.MachineModels = {
        build: build,
        styleFor: styleFor,
        airDemand: airDemand,
        footprint: footprint,
        styles: function () { return Object.keys(BUILDERS); },
        materials: MAT
    };
})(window);
