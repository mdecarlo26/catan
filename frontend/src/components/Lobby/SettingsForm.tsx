/**
 * Generic lobby settings form.
 *
 * Renders one form control per entry in a `SettingFieldMeta[]` registry
 * (mirroring backend/app/game/settings_schema.py's `SETTINGS_REGISTRY`
 * shape, typed via the existing `SettingFieldMeta` / `GameSettings` types
 * in src/types/protocol.ts -- see Lobby.mock.ts for a fixture registry
 * that mirrors the backend's actual entries).
 *
 * Adding a new toggle to the backend registry (and to `GameSettings`)
 * requires no changes here: the widget picked for a field is driven
 * entirely by its `type` ("int" -> number input, "bool" -> checkbox,
 * "string" -> select when `options` is known, else free-text input).
 *
 * Pure/presentational: takes the current settings values and the
 * registry as props, calls `onChange` with a full next `GameSettings`
 * object on every edit. No store/API imports -- Wave 2 wires this up by
 * passing live values + a dispatcher into `onChange`.
 */
import type { GameSettings, SettingFieldMeta } from "../../types/protocol";
import styles from "./SettingsForm.module.css";

/**
 * UI-only extension of the wire `SettingFieldMeta` shape: the backend
 * registry has no concept of "known values" for a string field (e.g.
 * `board_layout` is deliberately an open string key into a layout
 * registry -- see settings_schema.py), so callers that *do* know a
 * closed set of choices can supply them here to get a <select> instead
 * of a free-text input. This does not duplicate/rename `SettingFieldMeta`
 * or `GameSettings` -- it's additive metadata purely for widget choice.
 */
export interface LobbySettingFieldMeta extends SettingFieldMeta {
  /** Only consulted when `type === "string"`. */
  options?: readonly string[];
}

export interface SettingsFormProps {
  /** Ordered field metadata, e.g. SETTINGS_REGISTRY_MOCK from Lobby.mock.ts. */
  registry: readonly LobbySettingFieldMeta[];
  values: GameSettings;
  /** Called with the full next settings object after any single field edit. */
  onChange: (next: GameSettings) => void;
  /** Non-hosts get a read-only view of the same form. */
  disabled?: boolean;
}

/** Generic (typed-as-unknown) field assignment -- this is the one place
 * in the form that has to erase GameSettings' per-field types in order to
 * stay generic over the registry; every other consumer of GameSettings
 * keeps full typing. */
function withField(values: GameSettings, key: keyof GameSettings, value: unknown): GameSettings {
  return { ...values, [key]: value };
}

/** Friendly labels for `board_layout`'s raw wire values (see
 * settings_schema.py: "random"/"fixed" plus named layout-registry keys).
 * Any value without an entry here just falls back to showing the raw
 * string, so a newly-added layout key never breaks rendering. */
const BOARD_LAYOUT_LABELS: Record<string, string> = {
  random: "Random",
  fixed: "Fixed",
  standard: "Standard (3-4p)",
  expansion_5_6: "Expansion (5-6p)",
  extended_7_8: "Extended (7-8p)",
  two_player: "Two Player",
};

/**
 * Custom renderer for `board_layout`: a dropdown with clearly-labeled
 * options instead of the generic string select's raw wire values (typing
 * "expansion_5_6" by hand is bad UX for a friend group). Falls back to
 * just "Random"/"Fixed" if the registry entry didn't supply `options`.
 */
function renderBoardLayoutSelect(
  field: LobbySettingFieldMeta,
  values: GameSettings,
  onChange: (next: GameSettings) => void,
  disabled: boolean
): JSX.Element {
  const id = `setting-${field.key}`;
  const value = typeof values.board_layout === "string" ? values.board_layout : String(field.default ?? "random");
  const choices = field.options && field.options.length > 0 ? field.options : ["random", "fixed"];
  return (
    <select
      id={id}
      className={styles.select}
      value={value}
      disabled={disabled}
      onChange={(e) => onChange(withField(values, field.key, e.target.value))}
    >
      {choices.map((opt) => (
        <option key={opt} value={opt}>
          {BOARD_LAYOUT_LABELS[opt] ?? opt}
        </option>
      ))}
    </select>
  );
}

type FieldRenderer = (
  field: LobbySettingFieldMeta,
  values: GameSettings,
  onChange: (next: GameSettings) => void,
  disabled: boolean
) => JSX.Element;

/**
 * Per-field custom renderers, keyed by `SettingFieldMeta.key` -- an
 * escape hatch for a field whose generic type-driven widget (see
 * `renderControl` below) isn't good enough UX, without requiring a
 * backend schema change (a real enum-choices concept on
 * `SettingFieldMeta`) just for one field. `board_layout` is the only
 * entry today, but keeping this as a lookup table (rather than a
 * `field.key === "board_layout"` branch inline in `renderControl`) means
 * a second field can get the same treatment later by adding one more
 * entry here instead of another special case bolted on.
 */
const CUSTOM_FIELD_RENDERERS: Partial<Record<keyof GameSettings, FieldRenderer>> = {
  board_layout: renderBoardLayoutSelect,
};

export function SettingsForm({ registry, values, onChange, disabled = false }: SettingsFormProps) {
  return (
    <form className={styles.form} aria-label="Game settings">
      {registry.map((field) => (
        <div key={field.key} className={styles.row}>
          <label className={styles.label} htmlFor={`setting-${field.key}`}>
            {fieldLabel(field.key)}
          </label>
          {renderControl(field, values, onChange, disabled)}
          <p className={styles.description}>{field.description}</p>
        </div>
      ))}
    </form>
  );
}

function renderControl(
  field: LobbySettingFieldMeta,
  values: GameSettings,
  onChange: (next: GameSettings) => void,
  disabled: boolean
) {
  const customRenderer = CUSTOM_FIELD_RENDERERS[field.key];
  if (customRenderer) return customRenderer(field, values, onChange, disabled);

  const id = `setting-${field.key}`;
  const raw = values[field.key];

  switch (field.type) {
    case "int": {
      const value = typeof raw === "number" ? raw : Number(field.default ?? 0);
      return (
        <input
          id={id}
          className={styles.numberInput}
          type="number"
          value={value}
          min={field.min_value ?? undefined}
          max={field.max_value ?? undefined}
          disabled={disabled}
          onChange={(e) => {
            const next = e.target.valueAsNumber;
            onChange(withField(values, field.key, Number.isNaN(next) ? value : next));
          }}
        />
      );
    }
    case "bool": {
      const value = Boolean(raw);
      return (
        <input
          id={id}
          className={styles.checkbox}
          type="checkbox"
          checked={value}
          disabled={disabled}
          onChange={(e) => onChange(withField(values, field.key, e.target.checked))}
        />
      );
    }
    case "string":
    default: {
      const value = typeof raw === "string" ? raw : String(field.default ?? "");
      if (field.options && field.options.length > 0) {
        return (
          <select
            id={id}
            className={styles.select}
            value={value}
            disabled={disabled}
            onChange={(e) => onChange(withField(values, field.key, e.target.value))}
          >
            {field.options.map((opt) => (
              <option key={opt} value={opt}>
                {opt}
              </option>
            ))}
          </select>
        );
      }
      return (
        <input
          id={id}
          className={styles.textInput}
          type="text"
          value={value}
          disabled={disabled}
          onChange={(e) => onChange(withField(values, field.key, e.target.value))}
        />
      );
    }
  }
}

function fieldLabel(key: keyof GameSettings): string {
  return key
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}
