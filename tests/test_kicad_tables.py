import unittest
from pathlib import Path
import shutil

from boardwright.kicad_tables import prepare_pcb_tables


class KiCadTablesTests(unittest.TestCase):
    def test_prepare_pcb_tables_writes_component_count_without_kibot_report(
        self,
    ) -> None:
        root = Path("tests/.tmp_kicad_tables")
        if root.exists():
            shutil.rmtree(root)
        root.mkdir()
        try:
            (root / "boardwright.kicad_pro").write_text("{}", encoding="utf-8")
            (root / "boardwright.kicad_pcb").write_text(
                """
(kicad_pcb
  (footprint "Resistor_SMD:R_0603" (layer "F.Cu")
    (attr smd)
    (fp_text reference "R1" (at 0 0 0) (layer "F.SilkS")
      (effects (font (face "Arial") (size 1 1)))))
  (footprint "Capacitor_SMD:C_0603" (layer "B.Cu")
    (attr smd)
    (fp_text reference "C1" (at 0 0 0) (layer "B.SilkS")
      (effects (font (face "Arial") (size 1 1)))))
  (footprint "Connector_PinHeader:PinHeader_1x02" (layer "F.Cu")
    (attr through_hole)
    (fp_text reference "J1" (at 0 0 0) (layer "F.SilkS")
      (effects (font (face "Arial") (size 1 1)))))
  (gr_text_box ""
    (start 0 0)
    (end 10 10)
    (uuid "511273fd-c939-4feb-bf05-ae1b43c3644e")
  )
  (gr_rect
    (start 0 0)
    (end 40 16)
    (layer "User.4")
    (uuid "8cb5a7ba-335d-4917-9b0b-efa4a7d38e40")
  )
  (gr_text "IMPEDANCE TABLE"
    (at 0 20 0)
    (layer "User.6")
    (uuid "afcafcfb-b3fc-4338-9b9a-917f96a8ecdc")
  )
  (gr_rect
    (start 0 21)
    (end 40 30)
    (layer "User.6")
    (uuid "9af73f77-a717-4896-ac91-e0684a71d0ea")
  )
  (group "kibot_table_csv_impedance_table"
    (uuid "97c94a25-9c9f-48a5-a72f-6473f597f678")
    (members "9af73f77-a717-4896-ac91-e0684a71d0ea" "afcafcfb-b3fc-4338-9b9a-917f96a8ecdc")
  )
)
""",
                encoding="utf-8",
            )
            (root / "project.kicad_sch").write_text(
                """
(kicad_sch
  (symbol (lib_id "Device:R") (in_bom yes) (on_board yes) (dnp no)
    (property "Reference" "R1") (property "Value" "10k"))
  (symbol (lib_id "Device:R") (in_bom yes) (on_board yes) (dnp no)
    (property "Reference" "R2") (property "Value" "10k"))
  (symbol (lib_id "Device:C") (in_bom yes) (on_board yes) (dnp no)
    (property "Reference" "C1") (property "Value" "100n"))
  (symbol (lib_id "power:GND")
    (property "Reference" "#PWR01") (property "Value" "GND"))
  (symbol (lib_id "Device:LED") (in_bom yes) (on_board yes) (dnp yes)
    (property "Reference" "D1") (property "Value" "LED"))
)
""",
                encoding="utf-8",
            )
            config_dir = root / ".boardwright"
            config_dir.mkdir()
            (config_dir / "project.yaml").write_text(
                """project:
  id: DEMO
  name: Demo Project
  board_name: Demo Board
  board_revision: B
  company: Demo Co
  designer: A. Designer
  git_url: ""
  github_repo: owner/demo
variants:
  dev_default: DRAFT
  preview_default: CHECKED
outputs:
  preview_engine: github-actions
assets:
  logo: assets/logo.png
""",
                encoding="utf-8",
            )
            (config_dir / "branches.yaml").write_text(
                "branches:\n  development: dev\n  preview: preview\n  release: main\n",
                encoding="utf-8",
            )
            (config_dir / "legal.yaml").write_text("legal: {}\n", encoding="utf-8")
            (config_dir / "revision_history.yaml").write_text(
                "revision_history: {}\n",
                encoding="utf-8",
            )
            kibot_dir = root / "boardwright_resources" / "kibot" / "yaml"
            kibot_dir.mkdir(parents=True)
            (kibot_dir / "kibot_main.yaml").write_text(
                """definitions:
  PROJECT_NAME: PROJECT NAME
  BOARD_NAME: BOARD NAME
  BOARD_REVISION: A
  COMPANY: COMPANY
  DESIGNER: F. LAST
  LOGO: 'assets/logos/rd-logo.png'
  GIT_URL: ''
""",
                encoding="utf-8",
            )

            result = prepare_pcb_tables(root)

            self.assertEqual(result.total, 3)
            self.assertEqual(
                result.rows,
                (("THT", 1, 0, 1), ("SMT", 1, 1, 2), ("Total", 2, 1, 3)),
            )
            self.assertEqual(
                result.csv_path.read_text(encoding="utf-8").replace("\r\n", "\n"),
                "Type,Front Side,Back Side,Total\nTHT,1,0,1\nSMT,1,1,2\nTotal,2,1,3\n",
            )

            pcb = (root / "boardwright.kicad_pcb").read_text(encoding="utf-8")
            self.assertIn('(gr_text "Front Side"', pcb)
            self.assertIn('(gr_text "Back Side"', pcb)
            self.assertIn('(gr_text "THT"', pcb)
            self.assertIn('(gr_text "SMT"', pcb)
            self.assertEqual(4, pcb.count("(gr_line"))
            self.assertIn("(justify left)", pcb)
            self.assertNotIn("(justify center", pcb)
            self.assertIn("(bold yes)", pcb)
            self.assertIn('face "Arial"', pcb)
            self.assertIn("IMPEDANCE TABLE", pcb)
            self.assertIn("NO IMPEDANCE CONTROLLED TRACES", pcb)
            self.assertIn("kibot_table_csv_impedance_table", pcb)
            fabrication_notes = (
                root
                / "Manufacturing"
                / "Fabrication"
                / "boardwright-fabrication_notes.txt"
            ).read_text(encoding="utf-8")
            self.assertNotIn("${bb_w_mm}", fabrication_notes)
            self.assertIn("NO IMPEDANCE", pcb)
            self.assertNotIn("REFER TO IMPEDANCE TABLE", fabrication_notes)
            self.assertTrue(
                (root / "Manufacturing" / "Assembly" / "boardwright-assembly_notes.txt").is_file()
            )
            kibot_main = (kibot_dir / "kibot_main.yaml").read_text(encoding="utf-8")
            self.assertIn("PROJECT_NAME: 'Demo Project'", kibot_main)
            self.assertIn("BOARD_NAME: 'Demo Board'", kibot_main)
            self.assertIn("BOARD_REVISION: 'B'", kibot_main)
            self.assertIn("COMPANY: 'Demo Co'", kibot_main)
            self.assertIn("GIT_URL: 'https://github.com/owner/demo'", kibot_main)
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
