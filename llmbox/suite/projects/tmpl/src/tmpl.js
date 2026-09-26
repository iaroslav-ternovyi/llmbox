// Template engine - see README.md for the full specification.
class TemplateError extends Error {}

function render(template, data) {
  throw new Error("not implemented");
}

module.exports = { render, TemplateError };
