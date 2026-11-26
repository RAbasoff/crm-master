/* air_network.js — compressed-air distribution rendered in the 3D factory map.
 *
 * Ring main + zone branches come from static/moeskroen_air_network.json
 * (produced by work/_bim_moeskroen/air_calc.py). Drops are computed on the fly:
 * for every placed machine that actually consumes air, an orthogonal pipe runs
 * from the nearest point of the ring main to the machine and drops to 1.2 m.
 *
 * Exposes: window.AirNetwork = {
 *   setNetwork(net), rebuild(machines) -> THREE.Group,  visible(bool)
 * }
 */
(function (global) {
    'use strict';

    var MAIN_H = 4.5;      // m — mains run under the 10 m ceiling
    var DROP_H = 1.2;      // m — end height at the machine
    // Radii are exaggerated ~3x so the pipework reads at building scale
    // (real DN65 = 69 mm, which is sub-pixel next to a 76 m factory).
    var R_MAIN = 0.105;    // DN65
    var R_BRANCH = 0.058;  // DN25
    var R_DROP = 0.036;    // DN15/20

    var COL_MAIN = 0x0a5ad2;
    var COL_BRANCH = 0x0090c8;
    var COL_DROP = 0xe67800;
    var COL_COMP = 0xc82323;

    var net = null;

    function rFor(fad) {
        if (fad >= 20) return 0.058;    // DN25
        if (fad >= 6) return 0.045;     // DN20
        return 0.036;                   // DN15
    }

    function tube(THREE, g, pts, r, color) {
        if (!pts || pts.length < 2) return;
        var mat = new THREE.MeshStandardMaterial({
            color: color, metalness: 0.55, roughness: 0.35
        });
        for (var i = 0; i < pts.length - 1; i++) {
            var a = new THREE.Vector3(pts[i][0], pts[i][1], pts[i][2]);
            var b = new THREE.Vector3(pts[i + 1][0], pts[i + 1][1], pts[i + 1][2]);
            var dir = b.clone().sub(a), L = dir.length();
            if (L < 0.02) continue;
            var m = new THREE.Mesh(new THREE.CylinderGeometry(r, r, L, 10), mat);
            m.position.copy(a).add(b).multiplyScalar(0.5);
            m.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir.normalize());
            g.add(m);
        }
        pts.forEach(function (p) {
            var s = new THREE.Mesh(new THREE.SphereGeometry(r * 1.5, 8, 6), mat);
            s.position.set(p[0], p[1], p[2]);
            g.add(s);
        });
    }

    function box(THREE, g, w, h, d, x, y, z, color, opacity) {
        var mat = new THREE.MeshStandardMaterial({
            color: color, transparent: opacity != null, opacity: opacity == null ? 1 : opacity,
            roughness: 0.6, metalness: 0.2
        });
        var m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), mat);
        m.position.set(x, y, z);
        g.add(m);
        return m;
    }

    /** closest point on the ring polyline to (x,z); returns [x, z, index] */
    function nearestOnRing(x, z) {
        var best = null, bestD = Infinity;
        var ring = net.ring;
        for (var i = 0; i < ring.length - 1; i++) {
            var ax = ring[i][0], az = ring[i][1];
            var bx = ring[i + 1][0], bz = ring[i + 1][1];
            var dx = bx - ax, dz = bz - az;
            var L2 = dx * dx + dz * dz;
            var t = L2 > 0 ? ((x - ax) * dx + (z - az) * dz) / L2 : 0;
            t = Math.max(0, Math.min(1, t));
            var qx = ax + dx * t, qz = az + dz * t;
            var dist = (x - qx) * (x - qx) + (z - qz) * (z - qz);
            if (dist < bestD) { bestD = dist; best = [qx, qz]; }
        }
        return best || [x, z];
    }

    /** orthogonal route: ring point -> (machine.x, ring.z) -> machine -> drop */
    function routeTo(x, z) {
        var p = nearestOnRing(x, z);
        var pts = [[p[0], MAIN_H, p[1]]];
        if (Math.abs(p[1] - z) > 0.4) pts.push([p[0], MAIN_H, z]);
        if (Math.abs(p[0] - x) > 0.4) pts.push([x, MAIN_H, z]);
        pts.push([x, DROP_H, z]);
        return pts;
    }

    function build(THREE, machines) {
        var g = new THREE.Group();
        if (!net) return g;

        // ring main
        tube(THREE, g, net.ring.map(function (p) { return [p[0], MAIN_H, p[1]]; }),
             R_MAIN, COL_MAIN);

        // zone branches
        (net.branches || []).forEach(function (b) {
            tube(THREE, g, [
                [b.anchor[0], MAIN_H, b.anchor[1]],
                [b.end[0], MAIN_H, b.end[1]]
            ], R_BRANCH, COL_BRANCH);
        });

        // compressor station block + feeder to the ring
        var c = net.compressor;
        box(THREE, g, c.room_w, 3.2, c.room_d, c.x, 1.6, c.z, COL_COMP, 0.22);
        box(THREE, g, c.room_w + 0.4, 0.3, c.room_d + 0.4, c.x, 3.35, c.z, COL_COMP, 0.35);
        var p = nearestOnRing(c.x, c.z);
        tube(THREE, g, [[c.x, 2.2, c.z], [p[0], 2.2, p[1]], [p[0], MAIN_H, p[1]]],
             R_MAIN, COL_COMP);

        // auto-routed drops to every air-consuming machine
        (machines || []).forEach(function (m) {
            var fad = 0;
            if (global.MachineModels && global.MachineModels.airDemand) {
                fad = global.MachineModels.airDemand(m);
            }
            if (!fad) return;
            tube(THREE, g, routeTo(m.x || 0, m.z || 0), rFor(fad), COL_DROP);
        });
        return g;
    }

    global.AirNetwork = {
        setNetwork: function (n) { net = n; },
        getNetwork: function () { return net; },
        rebuild: build
    };
})(window);
