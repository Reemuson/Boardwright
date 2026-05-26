import unittest
from pathlib import Path


class KiBotCiTests(unittest.TestCase):
    def test_pdf_outputs_use_repo_kicad_color_theme_resources(self) -> None:
        for path in (
            Path("boardwright_resources/kibot/yaml/kibot_out_pdf_schematic.yaml"),
            Path("boardwright_resources/kibot/yaml/kibot_main.yaml"),
        ):
            text = path.read_text(encoding="utf-8")

            self.assertNotIn("color_theme:", text, str(path))

        fabrication = Path(
            "boardwright_resources/kibot/yaml/kibot_out_pdf_fabrication.yaml"
        ).read_text(encoding="utf-8")
        assembly = Path(
            "boardwright_resources/kibot/yaml/kibot_out_pdf_assembly.yaml"
        ).read_text(encoding="utf-8")
        globals_yaml = Path(
            "boardwright_resources/kibot/yaml/kibot_globals.yaml"
        ).read_text(encoding="utf-8")

        self.assertIn("color_theme: '@COLOR_THEME@'", fabrication)
        self.assertIn("color_theme: '@COLOR_THEME@'", assembly)
        self.assertIn("COLOR_THEME: KiCad_Theme", fabrication)
        self.assertIn("COLOR_THEME: KiCad_Theme", assembly)
        self.assertIn("COLOR_THEME: KiCad_Theme", Path("boardwright_resources/kibot/yaml/kibot_main.yaml").read_text(encoding="utf-8"))
        self.assertIn("resources_dir: '@RESOURCES_DIR@'", globals_yaml)

    def test_pcb_template_keeps_intended_arial_faces(self) -> None:
        pcb = Path("boardwright.kicad_pcb").read_text(encoding="utf-8")

        self.assertIn('face "Arial"', pcb)

    def test_pdf_outputs_use_local_include_tables_without_theme(self) -> None:
        fabrication = Path(
            "boardwright_resources/kibot/yaml/kibot_out_pdf_fabrication.yaml"
        ).read_text(encoding="utf-8")
        assembly = Path(
            "boardwright_resources/kibot/yaml/kibot_out_pdf_assembly.yaml"
        ).read_text(encoding="utf-8")

        self.assertIn("include_table:", fabrication)
        self.assertNotIn("NAME_IMPEDANCE_TABLE", fabrication)
        self.assertNotIn("NAME_COMP_COUNT", fabrication)
        self.assertNotIn("include_table:", assembly)
        self.assertNotIn("NAME_COMP_COUNT", assembly)

    def test_fabrication_pdf_restores_drill_pair_page(self) -> None:
        fabrication = Path(
            "boardwright_resources/kibot/yaml/kibot_out_pdf_fabrication.yaml"
        ).read_text(encoding="utf-8")

        self.assertIn("drill:", fabrication)
        self.assertIn("repeat_for_layer: '@LAYER_DRILL_MAP@'", fabrication)
        self.assertIn("repeat_layers: 'drill_pairs'", fabrication)
        self.assertIn("sheet: 'DRILL DRAWING (%lp)'", fabrication)
        self.assertIn("layer_var: 'DRILL DRAWING %lp (SCALE @SCALING@:1)'", fabrication)

    def test_impedance_table_is_not_generated_by_default(self) -> None:
        main = Path("boardwright_resources/kibot/yaml/kibot_main.yaml").read_text(
            encoding="utf-8"
        )
        template = Path(
            "boardwright_resources/kibot/resources/templates/impedance_table.txt"
        ).read_text(encoding="utf-8")

        self.assertNotIn("CSV_IMPEDANCE_TABLE_OUTPUT", main)
        self.assertNotIn("impedance_table", main)
        self.assertIn("Transmission Line,", template)
        self.assertNotIn("Edge-Coupled", template)
        self.assertNotIn("Â", template)

    def test_default_manufacturing_notes_match_template_contract(self) -> None:
        fabrication = Path(
            "boardwright_resources/kibot/resources/templates/fabrication_notes.txt"
        ).read_text(encoding="utf-8")
        assembly = Path(
            "boardwright_resources/kibot/resources/templates/assembly_notes.txt"
        ).read_text(encoding="utf-8")
        preflight = Path(
            "boardwright_resources/kibot/yaml/kibot_pre_set_text_variables.yaml"
        ).read_text(encoding="utf-8")

        self.assertIn("ASSEMBLY NOTES (UNLESS OTHERWISE SPECIFIED)", assembly)
        self.assertIn("DO NOT POPULATE PARTS ARE MARKED WITH A RED CROSS.", assembly)
        self.assertIn("FABRICATION NOTES (UNLESS OTHERWISE SPECIFIED)", fabrication)
        self.assertIn("FABRICATE PER IPC-6012A CLASS 2.", fabrication)
        self.assertIn("BOARD SIZE", fabrication)
        self.assertIn("×", fabrication)
        self.assertNotIn("Ã", fabrication)
        self.assertIn("REPORT_TEMPLATE_DIR", preflight)
        self.assertIn("fabrication_notes.txt", preflight)
        self.assertIn("assembly_notes.txt", preflight)

    def test_table_fill_preflight_is_not_global(self) -> None:
        main = Path("boardwright_resources/kibot/yaml/kibot_main.yaml").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("kibot_pre_include_table.yaml", main)
        self.assertNotIn("include_table:", main)
        self.assertIn("- name: table_sources", main)
        self.assertNotIn("CSV_COMP_COUNT_OUPUT", main)
        self.assertNotIn("components_count", main)

    def test_pdf_outputs_plot_movable_table_placeholder_layers(self) -> None:
        fabrication = Path(
            "boardwright_resources/kibot/yaml/kibot_out_pdf_fabrication.yaml"
        ).read_text(encoding="utf-8")

        self.assertIn("LAYER_TP_LIST_TOP", fabrication)
        self.assertIn("LAYER_TP_LIST_BOTTOM", fabrication)
        self.assertIn("LAYER_DRILL_MAP", fabrication)
        self.assertIn("sheet: 'DRILL DRAWING (%lp)'", fabrication)

    def test_impedance_placeholder_group_contains_title_and_box(self) -> None:
        pcb = Path("boardwright.kicad_pcb").read_text(encoding="utf-8")

        self.assertIn('group "kibot_table_csv_impedance_table"', pcb)
        self.assertIn('"afcafcfb-b3fc-4338-9b9a-917f96a8ecdc"', pcb)

    def test_assembly_pdf_keeps_boardwright_component_count_placeholder_layer(self) -> None:
        assembly = Path(
            "boardwright_resources/kibot/yaml/kibot_out_pdf_assembly.yaml"
        ).read_text(encoding="utf-8")
        pcb = Path("boardwright.kicad_pcb").read_text(encoding="utf-8")

        self.assertIn("LAYER_ASSEMBLY_TEXT_TOP", assembly)
        self.assertIn("LAYER_ASSEMBLY_TEXT_BOTTOM", assembly)
        self.assertIn('group "kibot_table_csv_comp_count"', pcb)

    def test_readme_template_avoids_repo_specific_placeholders(self) -> None:
        template = Path(
            "boardwright_resources/kibot/resources/templates/readme.txt"
        ).read_text(encoding="utf-8")

        self.assertNotIn("/actions/workflows", template)
        self.assertNotIn("badge.svg", template)
        self.assertNotIn("Hardware photographs are not available", template)
        self.assertNotIn("DIRECTORY STRUCTURE", template)
        self.assertIn("Revision", template)
        self.assertIn("Board revision", template)
        self.assertIn("Release version", template)
        self.assertIn("## OUTPUTS", template)
        self.assertIn("Generated manufacturing, schematic, test, and release packages", template)

    def test_text_variables_separate_board_revision_release_version_and_source_hashes(self) -> None:
        preflight = Path(
            "boardwright_resources/kibot/yaml/kibot_pre_set_text_variables.yaml"
        ).read_text(encoding="utf-8")
        main = Path("boardwright_resources/kibot/yaml/kibot_main.yaml").read_text(
            encoding="utf-8"
        )

        self.assertIn("variable: 'REVISION'", preflight)
        self.assertIn(".boardwright/release.env", preflight)
        self.assertIn("RELEASE_VERSION", preflight)
        self.assertIn("git describe --tags --exact-match", preflight)
        self.assertIn("variable: 'BOARD_REVISION'", preflight)
        self.assertIn("text: '@BOARD_REVISION@'", preflight)
        self.assertIn("variable: 'RELEASE_VERSION'", preflight)
        self.assertIn('git log --no-merges -1 --format="%h" -- "$KIBOT_SCH_NAME"', preflight)
        self.assertIn('git log --no-merges -1 --format="%h" -- "$KIBOT_PCB_NAME"', preflight)
        self.assertIn("BOARD_REVISION: @BOARD_REVISION@", main)
        self.assertIn("BOARD_REVISION: A", main)


if __name__ == "__main__":
    unittest.main()
