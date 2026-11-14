export interface ContrastRow {
  theme: "dark" | "light";
  group: string;
  label: string;
  fg: string;
  bg: string;
  ratio: number;
  min: number;
  pass: boolean;
}

export function parseThemes(css: string): {
  dark: Record<string, string>;
  light: Record<string, string>;
};
export function parseHex(value: string | undefined): number[] | null;
export function luminance(rgb: number[]): number;
export function contrastRatio(a: number[], b: number[]): number;
export function pairs(): Array<[string, string, string, string, number]>;
export function evaluate(css: string): ContrastRow[];
