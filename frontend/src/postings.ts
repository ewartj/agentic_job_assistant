/** Posting names, by the same rule as the API: letters, digits, '_' and '-', starting with a letter or digit. */
export const NAME = /^[A-Za-z0-9][A-Za-z0-9_-]*$/;

/** A name from the company and role, e.g. "Acme Health" + "Data Scientist" -> "acme_health_data_scientist". */
export function suggestName(company: string, role: string): string {
  return `${company} ${role}`
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 60);
}
