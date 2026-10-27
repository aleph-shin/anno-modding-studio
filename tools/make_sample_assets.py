"""Generate a small but realistic sample assets.xml (and texts file).

Useful for trying the app or running the tests without owning the game files.

    python tools/make_sample_assets.py sample/
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

PRODUCTS = [
    (1010196, "wood", "Timber"),
    (1010197, "brick", "Bricks"),
    (1010202, "steel", "Steel"),
    (1010204, "sail", "Sails"),
    (1010207, "window", "Windows"),
    (1010208, "beer", "Beer"),
    (1010212, "work_clothes", "Work Clothes"),
    (1010214, "canned_food", "Canned Food"),
]

RESIDENCES = [
    (1010030, "residence_tier01", "Farmer Residence", 10, 8),
    (1010031, "residence_tier02", "Worker Residence", 20, 14),
    (1010032, "residence_tier03", "Artisan Residence", 30, 20),
    (1010033, "residence_tier04", "Engineer Residence", 40, 26),
]

FACTORIES = [
    (1010340, "lumberjack", "Lumberjack's Hut", 30.0, 1),
    (1010341, "sawmill", "Sawmill", 45.0, 2),
    (1010342, "brick_factory", "Brick Factory", 60.0, 3),
    (1010343, "steel_works", "Steelworks", 90.0, 4),
    (1010344, "weaponry", "Weapons Factory", 120.0, 5),
]


def _indent(level: int) -> str:
    return "  " * level


def _asset_residence(guid, name, loca, capacity, upkeep, level):
    i = _indent
    out = [
        "%s<Asset>" % i(level),
        "%s<Template>ResidenceBuilding7</Template>" % i(level + 1),
        "%s<Values>" % i(level + 1),
        "%s<Standard>" % i(level + 2),
        "%s<GUID>%d</GUID>" % (i(level + 3), guid),
        "%s<Name>%s</Name>" % (i(level + 3), name),
        "%s<IconFilename>data/ui/2kimages/main/3dicons/icon_%s.png</IconFilename>"
        % (i(level + 3), name),
        "%s</Standard>" % i(level + 2),
        "%s<Text>" % i(level + 2),
        "%s<LocaText>" % i(level + 3),
        "%s<English>" % i(level + 4),
        "%s<Text>%s</Text>" % (i(level + 5), loca),
        "%s</English>" % i(level + 4),
        "%s</LocaText>" % i(level + 3),
        "%s</Text>" % i(level + 2),
        "%s<Building>" % i(level + 2),
        "%s<BuildingType>Residence</BuildingType>" % i(level + 3),
        "%s<TerrainType>Land</TerrainType>" % i(level + 3),
        "%s</Building>" % i(level + 2),
        "%s<Residence7>" % i(level + 2),
        "%s<ResidenceUpgrade>%d</ResidenceUpgrade>" % (i(level + 3), capacity),
        "%s<Residents>%d</Residents>" % (i(level + 3), upkeep),
        "%s<AllowChangeResidents>1</AllowChangeResidents>" % i(level + 3),
        "%s</Residence7>" % i(level + 2),
        "%s<Cost>" % i(level + 2),
        "%s<Costs>" % i(level + 3),
    ]
    for pguid, pname, _ploca in PRODUCTS[:3]:
        out += [
            "%s<Item>" % i(level + 4),
            "%s<Ingredient>%d</Ingredient>" % (i(level + 5), pguid),
            "%s<Amount>%d</Amount>" % (i(level + 5), 5 + (guid % 7)),
            "%s</Item>" % i(level + 4),
        ]
    out += [
        "%s</Costs>" % i(level + 3),
        "%s</Cost>" % i(level + 2),
        # A list with no identifying child, to exercise the positional fallback.
        "%s<Tags>" % i(level + 2),
        "%s<Tag>residence</Tag>" % i(level + 3),
        "%s<Tag>housing</Tag>" % i(level + 3),
        "%s</Tags>" % i(level + 2),
        "%s</Values>" % i(level + 1),
        "%s</Asset>" % i(level),
    ]
    return out


def _asset_factory(guid, name, loca, cycle, product_index, level):
    i = _indent
    pguid, _pname, _ploca = PRODUCTS[product_index % len(PRODUCTS)]
    out = [
        "%s<Asset>" % i(level),
        "%s<Template>FactoryBuilding7</Template>" % i(level + 1),
        "%s<Values>" % i(level + 1),
        "%s<Standard>" % i(level + 2),
        "%s<GUID>%d</GUID>" % (i(level + 3), guid),
        "%s<Name>%s</Name>" % (i(level + 3), name),
        "%s</Standard>" % i(level + 2),
        "%s<Text>" % i(level + 2),
        "%s<LocaText>" % i(level + 3),
        "%s<English>" % i(level + 4),
        "%s<Text>%s</Text>" % (i(level + 5), loca),
        "%s</English>" % i(level + 4),
        "%s</LocaText>" % i(level + 3),
        "%s</Text>" % i(level + 2),
        "%s<Building>" % i(level + 2),
        "%s<BuildingType>Factory</BuildingType>" % i(level + 3),
        "%s<BuildModeStartLevel>1</BuildModeStartLevel>" % i(level + 3),
        "%s</Building>" % i(level + 2),
        "%s<Factory7>" % i(level + 2),
        "%s<CycleTime>%.2f</CycleTime>" % (i(level + 3), cycle),
        "%s<FactoryOutputs>" % i(level + 3),
        "%s<Item>" % i(level + 4),
        "%s<Product>%d</Product>" % (i(level + 5), pguid),
        "%s<Amount>1</Amount>" % i(level + 5),
        "%s<StorageAmount>50</StorageAmount>" % i(level + 5),
        "%s</Item>" % i(level + 4),
        "%s</FactoryOutputs>" % i(level + 3),
        "%s<FactoryInputs>" % i(level + 3),
    ]
    for pg, _pn, _pl in PRODUCTS[: max(1, product_index % 3)]:
        out += [
            "%s<Item>" % i(level + 4),
            "%s<Product>%d</Product>" % (i(level + 5), pg),
            "%s<Amount>1</Amount>" % i(level + 5),
            "%s<StorageAmount>50</StorageAmount>" % i(level + 5),
            "%s</Item>" % i(level + 4),
        ]
    out += [
        "%s</FactoryInputs>" % i(level + 3),
        "%s</Factory7>" % i(level + 2),
        "%s<Maintenance>" % i(level + 2),
        "%s<Maintenances>" % i(level + 3),
        "%s<Item>" % i(level + 4),
        "%s<Product>1010017</Product>" % i(level + 5),
        "%s<Amount>%d</Amount>" % (i(level + 5), 20 * (product_index + 1)),
        "%s</Item>" % i(level + 4),
        "%s<Item>" % i(level + 4),
        "%s<Product>1010365</Product>" % i(level + 5),
        "%s<Amount>%d</Amount>" % (i(level + 5), product_index + 1),
        "%s</Item>" % i(level + 4),
        # Real assets pad their lists with empty entries and carry unused,
        # empty sections; the UI has to cope with both.
        "%s<Item/>" % i(level + 4),
        "%s<Item/>" % i(level + 4),
        "%s</Maintenances>" % i(level + 3),
        "%s</Maintenance>" % i(level + 2),
        "%s<BuildingUpgrade/>" % i(level + 2),
        "%s<ItemAction/>" % i(level + 2),
        "%s</Values>" % i(level + 1),
        "%s</Asset>" % i(level),
    ]
    return out


def _asset_product(guid, name, loca, level):
    i = _indent
    return [
        "%s<Asset>" % i(level),
        "%s<Template>Product</Template>" % i(level + 1),
        "%s<Values>" % i(level + 1),
        "%s<Standard>" % i(level + 2),
        "%s<GUID>%d</GUID>" % (i(level + 3), guid),
        "%s<Name>%s</Name>" % (i(level + 3), name),
        "%s</Standard>" % i(level + 2),
        "%s<Text>" % i(level + 2),
        "%s<LocaText>" % i(level + 3),
        "%s<English>" % i(level + 4),
        "%s<Text>%s</Text>" % (i(level + 5), loca),
        "%s</English>" % i(level + 4),
        "%s</LocaText>" % i(level + 3),
        "%s</Text>" % i(level + 2),
        "%s<Product>" % i(level + 2),
        "%s<ProductGroup>Raw</ProductGroup>" % i(level + 3),
        "%s<BaseAmount>1</BaseAmount>" % i(level + 3),
        "%s<TradePrice>%d</TradePrice>" % (i(level + 3), 100 + guid % 400),
        "%s</Product>" % i(level + 2),
        "%s</Values>" % i(level + 1),
        "%s</Asset>" % i(level),
    ]


def _filler_assets(start_guid, count, group_name, level):
    """Bulk assets so the browser has something to search through."""
    rng = random.Random(start_guid)
    out = []
    for n in range(count):
        guid = start_guid + n
        out += [
            "%s<Asset>" % _indent(level),
            "%s<Template>GenericItem</Template>" % _indent(level + 1),
            "%s<Values>" % _indent(level + 1),
            "%s<Standard>" % _indent(level + 2),
            "%s<GUID>%d</GUID>" % (_indent(level + 3), guid),
            "%s<Name>%s_item_%03d</Name>" % (_indent(level + 3), group_name.lower(), n),
            "%s</Standard>" % _indent(level + 2),
            "%s<Item>" % _indent(level + 2),
            "%s<Rarity>%s</Rarity>"
            % (_indent(level + 3), rng.choice(["Common", "Uncommon", "Rare", "Epic"])),
            "%s<Allocation>Airship</Allocation>" % _indent(level + 3),
            "%s</Item>" % _indent(level + 2),
            "%s</Values>" % _indent(level + 1),
            "%s</Asset>" % _indent(level),
        ]
    return out


def build_assets_xml(filler: int = 300) -> str:
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<Content>", "  <AssetList>", "    <Groups>"]

    # Buildings > Residence / Production
    lines += ["      <Group>", "        <Name>Buildings</Name>", "        <Groups>"]
    lines += ["          <Group>", "            <Name>Residence</Name>", "            <Assets>"]
    for guid, name, loca, cap, res in RESIDENCES:
        lines += _asset_residence(guid, name, loca, cap, res, 7)
    lines += ["            </Assets>", "          </Group>"]

    lines += ["          <Group>", "            <Name>Production</Name>", "            <Assets>"]
    for guid, name, loca, cycle, idx in FACTORIES:
        lines += _asset_factory(guid, name, loca, cycle, idx, 7)
    lines += ["            </Assets>", "          </Group>"]
    lines += ["        </Groups>", "      </Group>"]

    # Products
    lines += ["      <Group>", "        <Name>Products</Name>", "        <Assets>"]
    for guid, name, loca in PRODUCTS:
        lines += _asset_product(guid, name, loca, 5)
    lines += ["        </Assets>", "      </Group>"]

    # Items (bulk)
    lines += ["      <Group>", "        <Name>Items</Name>", "        <Groups>"]
    for block, (gname, start) in enumerate(
        [("Specialists", 190000), ("Equipment", 195000)]
    ):
        lines += [
            "          <Group>",
            "            <Name>%s</Name>" % gname,
            "            <Assets>",
        ]
        lines += _filler_assets(start, filler // 2, gname, 7)
        lines += ["            </Assets>", "          </Group>"]
    lines += ["        </Groups>", "      </Group>"]

    lines += ["    </Groups>", "  </AssetList>", "</Content>", ""]
    return "\n".join(lines)


def build_texts_xml() -> str:
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<TextExport>", "  <Texts>"]
    entries = (
        [(g, loca) for g, _n, loca, _c, _r in RESIDENCES]
        + [(g, loca) for g, _n, loca, _c, _i in FACTORIES]
        + [(g, loca) for g, _n, loca in PRODUCTS]
    )
    for guid, text in entries:
        lines += [
            "    <Text>",
            "      <GUID>%d</GUID>" % guid,
            "      <Text>%s</Text>" % text,
            "    </Text>",
        ]
    lines += ["  </Texts>", "</TextExport>", ""]
    return "\n".join(lines)


def main(argv):
    out_dir = Path(argv[1]) if len(argv) > 1 else Path("sample")
    filler = int(argv[2]) if len(argv) > 2 else 300
    out_dir.mkdir(parents=True, exist_ok=True)
    assets = out_dir / "assets.xml"
    texts = out_dir / "texts_english.xml"
    assets.write_text(build_assets_xml(filler), encoding="utf-8")
    texts.write_text(build_texts_xml(), encoding="utf-8")
    print("wrote %s (%.1f KB)" % (assets, assets.stat().st_size / 1024))
    print("wrote %s (%.1f KB)" % (texts, texts.stat().st_size / 1024))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
