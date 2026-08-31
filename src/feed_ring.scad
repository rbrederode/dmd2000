// Dimensions in mm
outer_diameter = 300;
inner_diameter = 200;
thickness      = 10;

// Rectangular cutout dimensions
cutout_depth  = 10;   // 1 cm into the ring
cutout_width  = 40;   // 4 cm along the inner edge

difference() {

    // Outer disc
    cylinder(
        d = outer_diameter,
        h = thickness,
        $fn = 200
    );

    // Central circular cutout
    translate([0, 0, -1])
        cylinder(
            d = inner_diameter,
            h = thickness + 2,
            $fn = 200
        );

    // Rectangular cutout from inner edge
    translate([
        inner_diameter / 2 - 10,
        -cutout_width / 2,
        -1
    ])
        cube([
            cutout_depth + 10,
            cutout_width,
            thickness + 2
        ]);
}