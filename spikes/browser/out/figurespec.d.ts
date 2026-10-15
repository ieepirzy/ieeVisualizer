// Generated from ieeVisualizer.spec.json_schema() by spikes/browser/schema-check.mjs

export type Annotation =
  | {
      label?: string | null;
      style?: {
        [k: string]: unknown;
      };
      text?: string;
      type: "text";
      x?: number | string | null;
      y?: number | string | null;
      z?: number | string | null;
    }
  | {
      axis?: "x" | "y" | "z";
      label?: string | null;
      style?: {
        [k: string]: unknown;
      };
      type: "reference_line";
      value?: number | string | null;
    }
  | {
      axis?: "x" | "y" | "z";
      end?: number | string | null;
      label?: string | null;
      start?: number | string | null;
      style?: {
        [k: string]: unknown;
      };
      type: "reference_band";
    };
export type Mark =
  | {
      data?: string | null;
      encoding?: {
        [k: string]: Encoding;
      };
      name?: string | null;
      role?: "observed" | "forecast" | "reference";
      style?: {
        [k: string]: unknown;
      };
      type: "line";
    }
  | {
      data?: string | null;
      encoding?: {
        [k: string]: Encoding;
      };
      name?: string | null;
      role?: "observed" | "forecast" | "reference";
      style?: {
        [k: string]: unknown;
      };
      type: "scatter";
    }
  | {
      data?: string | null;
      encoding?: {
        [k: string]: Encoding;
      };
      name?: string | null;
      role?: "observed" | "forecast" | "reference";
      style?: {
        [k: string]: unknown;
      };
      type: "area";
    }
  | {
      data?: string | null;
      encoding?: {
        [k: string]: Encoding;
      };
      name?: string | null;
      orientation?: "vertical" | "horizontal";
      role?: "observed" | "forecast" | "reference";
      style?: {
        [k: string]: unknown;
      };
      type: "bar";
    }
  | {
      /**
       * @minItems 2
       */
      bin_edges?: [number, number, ...number[]] | null;
      bins?: number | null;
      data?: string | null;
      density?: boolean;
      encoding?: {
        [k: string]: Encoding;
      };
      name?: string | null;
      role?: "observed" | "forecast" | "reference";
      style?: {
        [k: string]: unknown;
      };
      type: "histogram";
    }
  | {
      data?: string | null;
      encoding?: {
        [k: string]: Encoding;
      };
      name?: string | null;
      role?: "observed" | "forecast" | "reference";
      style?: {
        [k: string]: unknown;
      };
      type: "heatmap";
    }
  | {
      data?: string | null;
      encoding?: {
        [k: string]: Encoding;
      };
      name?: string | null;
      normalize?: boolean;
      role?: "observed" | "forecast" | "reference";
      scale?: number | null;
      style?: {
        [k: string]: unknown;
      };
      type: "quiver";
    }
  | {
      data?: string | null;
      domain?: {
        /**
         * @minItems 2
         * @maxItems 2
         */
        [k: string]: [number, number];
      };
      encoding?: {
        [k: string]: Encoding;
      };
      expression?: string | null;
      name?: string | null;
      parameters?: {
        [k: string]: number;
      };
      role?: "observed" | "forecast" | "reference";
      samples?: number;
      style?: {
        [k: string]: unknown;
      };
      type: "function";
      /**
       * @minItems 1
       */
      variables?: [string, ...string[]];
    };
export type Encoding =
  | string
  | {
      field: string;
      title?: string | null;
    };

export interface IeeVisualizerFigureSpec {
  annotations?: Annotation[];
  axes?: {
    [k: string]: Axis;
  };
  caption?: string | null;
  coordinate_system?: "cartesian2d" | "cartesian3d" | "polar";
  datasets?: {
    [k: string]: Snapshot | Ref;
  };
  id?: string | null;
  layers?: Mark[];
  legend?: {
    position?:
      | "auto"
      | "top"
      | "bottom"
      | "left"
      | "right"
      | "upper-left"
      | "upper-right"
      | "lower-left"
      | "lower-right"
      | "outside-right";
    show?: boolean;
    title?: string | null;
  };
  metadata?: {
    [k: string]: unknown;
  };
  schema_version: "1.0";
  theme?: string | null;
  title?: string | null;
}
export interface Axis {
  label?: string | null;
  /**
   * @minItems 2
   * @maxItems 2
   */
  limits?: [number | string | null, number | string | null] | null;
  scale?: "linear" | "log" | "symlog" | "time" | "category" | null;
  unit?: string | null;
}
export interface Snapshot {
  columns: {
    [k: string]: Column;
  };
  provenance?: {
    extra?: {
      [k: string]: unknown;
    };
    notes?: string | null;
    query?: string | null;
    retrieved_at?: string | null;
    run_id?: string | null;
    source?: string | null;
    source_version?: string | null;
    timezone?: string | null;
  };
  type: "snapshot";
}
export interface Column {
  aggregation?: string | null;
  denominator?: string | null;
  description?: string | null;
  dtype?: "float" | "int" | "bool" | "str" | "datetime" | null;
  unit?: string | null;
  values: unknown[];
}
export interface Ref {
  columns?: {
    [k: string]: {
      aggregation?: string | null;
      denominator?: string | null;
      description?: string | null;
      dtype?: "float" | "int" | "bool" | "str" | "datetime" | null;
      unit?: string | null;
    };
  };
  id: string;
  kind: string;
  query?: string | null;
  retrieved_at?: string | null;
  type: "ref";
  version?: string | null;
}
