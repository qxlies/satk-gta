"""Named COL surface material IDs.

Names follow the San Andreas surface material list commonly used by tools and
`surfinfo.dat`. The numeric IDs are still valid for COL files; this enum just
gives them readable names.
"""

from __future__ import annotations

from enum import IntEnum


class ColMaterial(IntEnum):
    DEFAULT = 0
    TARMAC = 1
    TARMAC_DAMAGED = 2
    TARMAC_REALLY_DAMAGED = 3
    PAVEMENT = 4
    PAVEMENT_DAMAGED = 5
    GRAVEL = 6
    CONCRETE_DAMAGED = 7
    PAINTED_GROUND = 8
    GRASS_SHORT_LUSH = 9
    GRASS_MEDIUM_LUSH = 10
    GRASS_LONG_LUSH = 11
    GRASS_SHORT_DRY = 12
    GRASS_MEDIUM_DRY = 13
    GRASS_LONG_DRY = 14
    GOLF_GRASS_ROUGH = 15
    GOLF_GRASS_SMOOTH = 16
    STEEP_SLIDY_GRASS = 17
    STEEP_CLIFF = 18
    FLOWER_BED = 19
    MEADOW = 20
    WASTE_GROUND = 21
    WOODLAND_GROUND = 22
    VEGETATION = 23
    MUD_WET = 24
    MUD_DRY = 25
    DIRT = 26
    DIRT_TRACK = 27
    SAND_DEEP = 28
    SAND_MEDIUM = 29
    SAND_COMPACT = 30
    SAND_ARID = 31
    SAND_MORE = 32
    SAND_BEACH = 33
    CONCRETE_BEACH = 34
    ROCK_DRY = 35
    ROCK_WET = 36
    ROCK_CLIFF = 37
    WATER_RIVERBED = 38
    WATER_SHALLOW = 39
    CORN_FIELD = 40
    HEDGE = 41
    WOOD_CRATES = 42
    WOOD_SOLID = 43
    WOOD_THIN = 44
    GLASS = 45
    GLASS_WINDOWS_LARGE = 46
    GLASS_WINDOWS_SMALL = 47
    EMPTY_1 = 48
    EMPTY_2 = 49
    GARAGE_DOOR = 50
    THICK_METAL_PLATE = 51
    SCAFFOLD_POLE = 52
    LAMP_POST = 53
    METAL_GATE = 54
    METAL_CHAIN_FENCE = 55
    GIRDER = 56
    FIRE_HYDRANT = 57
    CONTAINER = 58
    NEWS_VENDOR = 59
    WHEELBASE = 60
    CARDBOARD_BOX = 61
    PED = 62
    CAR = 63
    CAR_PANEL = 64
    CAR_MOVING_COMPONENT = 65
    TRANSPARENT_CLOTH = 66
    RUBBER = 67
    PLASTIC = 68
    TRANSPARENT_STONE = 69
    WOOD_BENCH = 70
    CARPET = 71
    FLOORBOARD = 72
    STAIRS_WOOD = 73
    SAND = 74
    SAND_DENSE = 75
    SAND_ARID_ALT = 76
    SAND_COMPACT_ALT = 77
    SAND_ROCKY = 78
    SAND_BEACH_ALT = 79
    GRASS_SHORT = 80
    GRASS_MEADOW = 81
    GRASS_DRY = 82
    WOODLAND = 83
    WOOD_DENSE = 84
    ROADSIDE = 85
    ROADSIDE_DESERT = 86
    FLOWERBED = 87
    WASTE_GROUND_ALT = 88
    CONCRETE = 89
    OFFICE_DESK = 90
    SHELF_711_1 = 91
    SHELF_711_2 = 92
    SHELF_711_3 = 93
    RESTAURANT_TABLE = 94
    BAR_TABLE = 95
    UNDERWATER_LUSH = 96
    UNDERWATER_BARREN = 97
    UNDERWATER_CORAL = 98
    UNDERWATER_DEEP = 99
    RIVERBED = 100
    RUBBLE = 101
    BEDROOM_FLOOR = 102
    KITCHEN_FLOOR = 103
    LIVINGROOM_FLOOR = 104
    CORRIDOR_FLOOR = 105
    FLOOR_711 = 106
    FAST_FOOD_FLOOR = 107
    SKANKY_FLOOR = 108
    MOUNTAIN = 109
    MARSH = 110
    BUSHY = 111
    BUSHY_MIX = 112
    BUSHY_DRY = 113
    BUSHY_MID = 114
    GRASS_WEE_FLOWERS = 115
    GRASS_DRY_TALL = 116
    GRASS_LUSH_TALL = 117
    GRASS_GREEN_MIX = 118
    GRASS_BROWN_MIX = 119
    GRASS_LOW = 120
    GRASS_ROCKY = 121
    GRASS_SMALL_TREES = 122
    DIRT_ROCKY = 123
    DIRT_WEEDS = 124
    GRASS_WEEDS = 125
    RIVER_EDGE = 126
    POOLSIDE = 127
    FOREST_STUMPS = 128
    FOREST_STICKS = 129
    FOREST_LEAVES = 130
    DESERT_ROCKS = 131
    FOREST_DRY = 132
    SPARSE_FLOWERS = 133
    BUILDING_SITE = 134
    DOCKLANDS = 135
    INDUSTRIAL = 136
    INDUSTRIAL_JETTY = 137
    CONCRETE_LITTER = 138
    ALLEY_RUBBISH = 139
    JUNKYARD_PILES = 140
    JUNKYARD_GROUND = 141
    DUMP = 142
    CACTUS_DENSE = 143
    AIRPORT_GROUND = 144
    CORNFIELD = 145
    GRASS_LIGHT = 146
    GRASS_LIGHTER = 147
    GRASS_LIGHTER_2 = 148
    GRASS_MID_1 = 149
    GRASS_MID_2 = 150
    GRASS_DARK = 151
    GRASS_DARK_2 = 152
    GRASS_DIRT_MIX = 153
    RIVERBED_STONE = 154
    RIVERBED_SHALLOW = 155
    RIVERBED_WEEDS = 156
    SEAWEED = 157
    DOOR = 158
    PLASTIC_BARRIER = 159
    PARK_GRASS = 160
    STAIRS_STONE = 161
    STAIRS_METAL = 162
    STAIRS_CARPET = 163
    FLOOR_METAL = 164
    FLOOR_CONCRETE = 165
    BIN_BAG = 166
    THIN_METAL_SHEET = 167
    METAL_BARREL = 168
    PLASTIC_CONE = 169
    PLASTIC_DUMPSTER = 170
    METAL_DUMPSTER = 171
    WOOD_PICKET_FENCE = 172
    WOOD_SLATED_FENCE = 173
    WOOD_RANCH_FENCE = 174
    UNBREAKABLE_GLASS = 175
    HAY_BALE = 176
    GORE = 177
    RAIL_TRACK = 178

    @property
    def label(self) -> str:
        return _COL_MATERIAL_LABELS[self]


_COL_MATERIAL_LABELS: dict[ColMaterial, str] = {
    ColMaterial.DEFAULT: "Default",
    ColMaterial.TARMAC: "Tarmac",
    ColMaterial.TARMAC_DAMAGED: "Tarmac (damaged)",
    ColMaterial.TARMAC_REALLY_DAMAGED: "Tarmac (really damaged)",
    ColMaterial.PAVEMENT: "Pavement",
    ColMaterial.PAVEMENT_DAMAGED: "Pavement (damaged)",
    ColMaterial.GRAVEL: "Gravel",
    ColMaterial.CONCRETE_DAMAGED: "Concrete (damaged)",
    ColMaterial.PAINTED_GROUND: "Painted Ground",
    ColMaterial.GRASS_SHORT_LUSH: "Grass (short lush)",
    ColMaterial.GRASS_MEDIUM_LUSH: "Grass (medium lush)",
    ColMaterial.GRASS_LONG_LUSH: "Grass (long lush)",
    ColMaterial.GRASS_SHORT_DRY: "Grass (short dry)",
    ColMaterial.GRASS_MEDIUM_DRY: "Grass (medium dry)",
    ColMaterial.GRASS_LONG_DRY: "Grass (long dry)",
    ColMaterial.GOLF_GRASS_ROUGH: "Golf Grass (rough)",
    ColMaterial.GOLF_GRASS_SMOOTH: "Golf Grass (smooth)",
    ColMaterial.STEEP_SLIDY_GRASS: "Steep Slidy Grass",
    ColMaterial.STEEP_CLIFF: "Steep Cliff",
    ColMaterial.FLOWER_BED: "Flower Bed",
    ColMaterial.MEADOW: "Meadow",
    ColMaterial.WASTE_GROUND: "Waste Ground",
    ColMaterial.WOODLAND_GROUND: "Woodland Ground",
    ColMaterial.VEGETATION: "Vegetation",
    ColMaterial.MUD_WET: "Mud (wet)",
    ColMaterial.MUD_DRY: "Mud (dry)",
    ColMaterial.DIRT: "Dirt",
    ColMaterial.DIRT_TRACK: "Dirt Track",
    ColMaterial.SAND_DEEP: "Sand (deep)",
    ColMaterial.SAND_MEDIUM: "Sand (medium)",
    ColMaterial.SAND_COMPACT: "Sand (compact)",
    ColMaterial.SAND_ARID: "Sand (arid)",
    ColMaterial.SAND_MORE: "Sand (more)",
    ColMaterial.SAND_BEACH: "Sand (beach)",
    ColMaterial.CONCRETE_BEACH: "Concrete (beach)",
    ColMaterial.ROCK_DRY: "Rock (dry)",
    ColMaterial.ROCK_WET: "Rock (wet)",
    ColMaterial.ROCK_CLIFF: "Rock (cliff)",
    ColMaterial.WATER_RIVERBED: "Water (riverbed)",
    ColMaterial.WATER_SHALLOW: "Water (shallow)",
    ColMaterial.CORN_FIELD: "Corn Field",
    ColMaterial.HEDGE: "Hedge",
    ColMaterial.WOOD_CRATES: "Wood (crates)",
    ColMaterial.WOOD_SOLID: "Wood (solid)",
    ColMaterial.WOOD_THIN: "Wood (thin)",
    ColMaterial.GLASS: "Glass",
    ColMaterial.GLASS_WINDOWS_LARGE: "Glass Windows (large)",
    ColMaterial.GLASS_WINDOWS_SMALL: "Glass Windows (small)",
    ColMaterial.EMPTY_1: "Empty1",
    ColMaterial.EMPTY_2: "Empty2",
    ColMaterial.GARAGE_DOOR: "Garage Door",
    ColMaterial.THICK_METAL_PLATE: "Thick Metal Plate",
    ColMaterial.SCAFFOLD_POLE: "Scaffold Pole",
    ColMaterial.LAMP_POST: "Lamp Post",
    ColMaterial.METAL_GATE: "Metal Gate",
    ColMaterial.METAL_CHAIN_FENCE: "Metal Chain fence",
    ColMaterial.GIRDER: "Girder",
    ColMaterial.FIRE_HYDRANT: "Fire Hydrant",
    ColMaterial.CONTAINER: "Container",
    ColMaterial.NEWS_VENDOR: "News Vendor",
    ColMaterial.WHEELBASE: "Wheelbase",
    ColMaterial.CARDBOARD_BOX: "Cardboard Box",
    ColMaterial.PED: "Ped",
    ColMaterial.CAR: "Car",
    ColMaterial.CAR_PANEL: "Car (panel)",
    ColMaterial.CAR_MOVING_COMPONENT: "Car (moving component)",
    ColMaterial.TRANSPARENT_CLOTH: "Transparent Cloth",
    ColMaterial.RUBBER: "Rubber",
    ColMaterial.PLASTIC: "Plastic",
    ColMaterial.TRANSPARENT_STONE: "Transparent Stone",
    ColMaterial.WOOD_BENCH: "Wood (bench)",
    ColMaterial.CARPET: "Carpet",
    ColMaterial.FLOORBOARD: "Floorboard",
    ColMaterial.STAIRS_WOOD: "Stairs (wood)",
    ColMaterial.SAND: "Sand",
    ColMaterial.SAND_DENSE: "Sand (dense)",
    ColMaterial.SAND_ARID_ALT: "Sand (arid)",
    ColMaterial.SAND_COMPACT_ALT: "Sand (compact)",
    ColMaterial.SAND_ROCKY: "Sand (rocky)",
    ColMaterial.SAND_BEACH_ALT: "Sand (beach)",
    ColMaterial.GRASS_SHORT: "Grass (short)",
    ColMaterial.GRASS_MEADOW: "Grass (meadow)",
    ColMaterial.GRASS_DRY: "Grass (dry)",
    ColMaterial.WOODLAND: "Woodland",
    ColMaterial.WOOD_DENSE: "Wood Dense",
    ColMaterial.ROADSIDE: "Roadside",
    ColMaterial.ROADSIDE_DESERT: "Roadside Des",
    ColMaterial.FLOWERBED: "Flowerbed",
    ColMaterial.WASTE_GROUND_ALT: "Waste Ground",
    ColMaterial.CONCRETE: "Concrete",
    ColMaterial.OFFICE_DESK: "Office Desk",
    ColMaterial.SHELF_711_1: "711 Shelf 1",
    ColMaterial.SHELF_711_2: "711 Shelf 2",
    ColMaterial.SHELF_711_3: "711 Shelf 3",
    ColMaterial.RESTAURANT_TABLE: "Restaurant Table",
    ColMaterial.BAR_TABLE: "Bar Table",
    ColMaterial.UNDERWATER_LUSH: "Underwater (lush)",
    ColMaterial.UNDERWATER_BARREN: "Underwater (barren)",
    ColMaterial.UNDERWATER_CORAL: "Underwater (coral)",
    ColMaterial.UNDERWATER_DEEP: "Underwater (deep)",
    ColMaterial.RIVERBED: "Riverbed",
    ColMaterial.RUBBLE: "Rubble",
    ColMaterial.BEDROOM_FLOOR: "Bedroom Floor",
    ColMaterial.KITCHEN_FLOOR: "Kitchen Floor",
    ColMaterial.LIVINGROOM_FLOOR: "Livingroom Floor",
    ColMaterial.CORRIDOR_FLOOR: "corridor Floor",
    ColMaterial.FLOOR_711: "711 Floor",
    ColMaterial.FAST_FOOD_FLOOR: "Fast Food Floor",
    ColMaterial.SKANKY_FLOOR: "Skanky Floor",
    ColMaterial.MOUNTAIN: "Mountain",
    ColMaterial.MARSH: "Marsh",
    ColMaterial.BUSHY: "Bushy",
    ColMaterial.BUSHY_MIX: "Bushy (mix)",
    ColMaterial.BUSHY_DRY: "Bushy (dry)",
    ColMaterial.BUSHY_MID: "Bushy (mid)",
    ColMaterial.GRASS_WEE_FLOWERS: "Grass (wee flowers)",
    ColMaterial.GRASS_DRY_TALL: "Grass (dry tall)",
    ColMaterial.GRASS_LUSH_TALL: "Grass (lush tall)",
    ColMaterial.GRASS_GREEN_MIX: "Grass (green mix)",
    ColMaterial.GRASS_BROWN_MIX: "Grass (brown mix)",
    ColMaterial.GRASS_LOW: "Grass (low)",
    ColMaterial.GRASS_ROCKY: "Grass (rocky)",
    ColMaterial.GRASS_SMALL_TREES: "Grass (small trees)",
    ColMaterial.DIRT_ROCKY: "Dirt (rocky)",
    ColMaterial.DIRT_WEEDS: "Dirt (weeds)",
    ColMaterial.GRASS_WEEDS: "Grass (weeds)",
    ColMaterial.RIVER_EDGE: "River Edge",
    ColMaterial.POOLSIDE: "Poolside",
    ColMaterial.FOREST_STUMPS: "Forest (stumps)",
    ColMaterial.FOREST_STICKS: "Forest (sticks)",
    ColMaterial.FOREST_LEAVES: "Forest (leaves)",
    ColMaterial.DESERT_ROCKS: "Desert Rocks",
    ColMaterial.FOREST_DRY: "Forest (dry)",
    ColMaterial.SPARSE_FLOWERS: "Sparse Flowers",
    ColMaterial.BUILDING_SITE: "Building Site",
    ColMaterial.DOCKLANDS: "Docklands",
    ColMaterial.INDUSTRIAL: "Industrial",
    ColMaterial.INDUSTRIAL_JETTY: "Industrial Jetty",
    ColMaterial.CONCRETE_LITTER: "Concrete (litter)",
    ColMaterial.ALLEY_RUBBISH: "Alley Rubbish",
    ColMaterial.JUNKYARD_PILES: "Junkyard Piles",
    ColMaterial.JUNKYARD_GROUND: "Junkyard Ground",
    ColMaterial.DUMP: "Dump",
    ColMaterial.CACTUS_DENSE: "Cactus Dense",
    ColMaterial.AIRPORT_GROUND: "Airport Ground",
    ColMaterial.CORNFIELD: "Cornfield",
    ColMaterial.GRASS_LIGHT: "Grass (light)",
    ColMaterial.GRASS_LIGHTER: "Grass (lighter)",
    ColMaterial.GRASS_LIGHTER_2: "Grass (lighter 2)",
    ColMaterial.GRASS_MID_1: "Grass (mid 1)",
    ColMaterial.GRASS_MID_2: "Grass (mid 2)",
    ColMaterial.GRASS_DARK: "Grass (dark)",
    ColMaterial.GRASS_DARK_2: "Grass (dark 2)",
    ColMaterial.GRASS_DIRT_MIX: "Grass (dirt mix)",
    ColMaterial.RIVERBED_STONE: "Riverbed (stone)",
    ColMaterial.RIVERBED_SHALLOW: "Riverbed (shallow)",
    ColMaterial.RIVERBED_WEEDS: "Riverbed (weeds)",
    ColMaterial.SEAWEED: "Seaweed",
    ColMaterial.DOOR: "Door",
    ColMaterial.PLASTIC_BARRIER: "Plastic Barrier",
    ColMaterial.PARK_GRASS: "Park Grass",
    ColMaterial.STAIRS_STONE: "Stairs (stone)",
    ColMaterial.STAIRS_METAL: "Stairs (metal)",
    ColMaterial.STAIRS_CARPET: "Stairs (carpet)",
    ColMaterial.FLOOR_METAL: "Floor (metal)",
    ColMaterial.FLOOR_CONCRETE: "Floor (concrete)",
    ColMaterial.BIN_BAG: "Bin Bag",
    ColMaterial.THIN_METAL_SHEET: "Thin Metal Sheet",
    ColMaterial.METAL_BARREL: "Metal Barrel",
    ColMaterial.PLASTIC_CONE: "Plastic Cone",
    ColMaterial.PLASTIC_DUMPSTER: "Plastic Dumpster",
    ColMaterial.METAL_DUMPSTER: "Metal Dumpster",
    ColMaterial.WOOD_PICKET_FENCE: "Wood Picket Fence",
    ColMaterial.WOOD_SLATED_FENCE: "Wood Slatted Fence",
    ColMaterial.WOOD_RANCH_FENCE: "Wood Ranch Fence",
    ColMaterial.UNBREAKABLE_GLASS: "Unbreakable Glass",
    ColMaterial.HAY_BALE: "Hay Bale",
    ColMaterial.GORE: "Gore",
    ColMaterial.RAIL_TRACK: "Rail Track",
}
