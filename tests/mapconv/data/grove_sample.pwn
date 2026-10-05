// satk mapconv sample (synthetic, written for the tests; Texture Studio export style).
// A bench corner on Grove Street: one removal, vanilla props, an SA-MP wall with a material and a sign.
RemoveBuildingForPlayer(playerid, 1265, 2488.8, -1684.79, 12.81, 1.0);
new tmpobjid;
CreateDynamicObject(1280, 2490.0, -1682.0, 12.85, 0.0, 0.0, 90.0); // parkbench1
CreateDynamicObject(1280, 2493.5, -1682.0, 12.85, 0.0, 0.0, 90.0, -1, -1, -1, 200.0, 150.0);
tmpobjid = CreateDynamicObject(19379, 2495.0, -1675.0, 12.4, 0.0, 90.0, 0.0, -1, -1, -1, 300.00, 300.00);
SetDynamicObjectMaterial(tmpobjid, 0, 1280, "benches_cj", "Metal3_128", 0xFF808080);
SetDynamicObjectMaterialText(tmpobjid, 1, "Grove Street", OBJECT_MATERIAL_SIZE_256x128, "Arial", 24, 1, 0xFF00FF00, 0x0, OBJECT_MATERIAL_TEXT_ALIGN_CENTER);
CreateObject(1215, 2492.0, -1680.0, 12.6, 0.0, 0.0, 0.0);
CreateObject(3594, 2500.0, -1670.0, 13.1, 3.0, -2.0, 120.0, 250.0); // la_fuckcar1, tilted
