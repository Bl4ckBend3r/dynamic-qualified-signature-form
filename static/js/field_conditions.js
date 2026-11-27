// Keep this predicate aligned with form_loader.evaluate_visible_if.
window.fieldConditionMatches = function (rule, current) {
  if (!rule) return true;
  if (typeof rule !== "object" || Array.isArray(rule)) return false;
  const operators = ["equals", "not_equals", "in", "not_in", "is_empty", "is_not_empty", "contains", "not_contains", "contains_any", "contains_all", "contains_none"];
  const operator = rule.operator || operators.find((key) => Object.hasOwn(rule, key)) || "equals";
  const expected = Object.hasOwn(rule, "value") ? rule.value : rule[operator];
  const equal = (a, b) => {
    if (Array.isArray(a) || Array.isArray(b)) return Array.isArray(a) && Array.isArray(b) && a.length === b.length && a.every((item, index) => equal(item, b[index]));
    if (a && b && typeof a === "object" && typeof b === "object") return Object.keys(a).length === Object.keys(b).length && Object.keys(a).every((key) => Object.hasOwn(b, key) && equal(a[key], b[key]));
    return a === b;
  };
  if (operator === "equals") return equal(current, expected);
  if (operator === "not_equals") return !equal(current, expected);
  if (operator === "in" || operator === "not_in") {
    const choices = Object.hasOwn(rule, "values") ? rule.values : expected;
    if (!Array.isArray(choices)) return false;
    const found = choices.some((choice) => equal(choice, current));
    return operator === "in" ? found : !found;
  }
  const empty = current == null || current === "" || (Array.isArray(current) && current.length === 0) || (typeof current === "object" && !Array.isArray(current) && Object.keys(current).length === 0);
  if (operator === "is_empty") return empty;
  if (operator === "is_not_empty") return !empty;
  if (operator === "contains" || operator === "not_contains") {
    if (!Array.isArray(current) || !current.every((value) => typeof value === "string") || typeof expected !== "string") return false;
    const found = current.includes(expected);
    return operator === "contains" ? found : !found;
  }
  if (["contains_any", "contains_all", "contains_none"].includes(operator)) {
    if (!Array.isArray(current) || !current.every((value) => typeof value === "string") || !Array.isArray(expected) || !expected.length || !expected.every((value) => typeof value === "string")) return false;
    if (operator === "contains_any") return expected.some((value) => current.includes(value));
    if (operator === "contains_all") return expected.every((value) => current.includes(value));
    return expected.every((value) => !current.includes(value));
  }
  return false;
};
