import fs from "node:fs";
import path from "node:path";
import { createHash } from "node:crypto";
import { describe, expect, it } from "vitest";
import ts from "typescript";

const routePath = path.resolve(process.cwd(), "app/api/[...path]/route.ts");
const reviewedRouteSha256 = "cc10e55daf455ea26ebbd9e7f2653f069f8223a5bdc1c449306d12fdd032886c";
const allowedIdentifierCalls = new Set(["encodeURIComponent", "fetch"]);
const allowedMemberCalls = new Set([
  "append",
  "get",
  "getSetCookie",
  "includes",
  "join",
  "map",
  "replace",
  "set",
  "toUpperCase",
]);

function assertThin(source: string): void {
  const file = ts.createSourceFile("route.ts", source, ts.ScriptTarget.Latest, true);
  let fetches = 0;
  let conditionals = 0;
  let conditionalExpressions = 0;
  let switchStatements = 0;
  let shortCircuitBranches = 0;
  let loops = 0;
  let tryStatements = 0;
  let transparentResponses = 0;
  const unexpectedCalls: string[] = [];

  function visit(node: ts.Node): void {
    if (ts.isIfStatement(node)) conditionals += 1;
    if (ts.isConditionalExpression(node)) conditionalExpressions += 1;
    if (ts.isSwitchStatement(node)) switchStatements += 1;
    if (
      ts.isBinaryExpression(node) &&
      [
        ts.SyntaxKind.AmpersandAmpersandToken,
        ts.SyntaxKind.BarBarToken,
        ts.SyntaxKind.QuestionQuestionToken,
      ].includes(node.operatorToken.kind)
    ) {
      shortCircuitBranches += 1;
    }
    if (ts.isForOfStatement(node)) loops += 1;
    if (ts.isTryStatement(node)) tryStatements += 1;
    if (ts.isCallExpression(node)) {
      if (ts.isIdentifier(node.expression)) {
        const name = node.expression.text;
        if (name === "fetch") fetches += 1;
        if (!allowedIdentifierCalls.has(name)) unexpectedCalls.push(name);
      } else if (ts.isPropertyAccessExpression(node.expression)) {
        const name = node.expression.name.text;
        if (!allowedMemberCalls.has(name)) unexpectedCalls.push(name);
      }
    }
    if (
      ts.isNewExpression(node) &&
      ts.isIdentifier(node.expression) &&
      node.expression.text === "Response" &&
      node.arguments?.[0]?.getText(file) === "upstream.body"
    ) {
      transparentResponses += 1;
    }
    ts.forEachChild(node, visit);
  }
  visit(file);

  expect(fetches, "the BFF must make exactly one upstream request").toBe(1);
  expect(conditionals, "new proxy branches require explicit invariant review").toBe(4);
  expect(conditionalExpressions, "ternary proxy branches are forbidden").toBe(0);
  expect(switchStatements, "switch-based proxy branches are forbidden").toBe(0);
  expect(shortCircuitBranches, "short-circuit proxy branches require explicit review").toBe(5);
  expect(loops, "new proxy loops require explicit invariant review").toBe(3);
  expect(tryStatements, "the proxy has one upstream-error boundary").toBe(1);
  expect(unexpectedCalls, "the proxy may only use transport-shaping calls").toEqual([]);
  expect(transparentResponses, "the success response must stream upstream.body unchanged").toBe(1);
}

describe("thin BFF AST contract", () => {
  const source = fs.readFileSync(routePath, "utf8");

  it("accepts the transparent transport proxy", () => {
    assertThin(source);
    expect(
      createHash("sha256").update(source).digest("hex"),
      "proxy source changed; explicitly review its branch semantics and update this digest",
    ).toBe(reviewedRouteSha256);
  });

  it("rejects inline domain branching", () => {
    const mutated = source.replace(
      "  const { path } = await context.params;",
      '  if (request.nextUrl.pathname.includes("attest")) return new Response("blocked");\n' +
        "  const { path } = await context.params;",
    );
    expect(() => assertThin(mutated)).toThrow("new proxy branches");
  });

  it("rejects a second upstream request", () => {
    const mutated = source.replace(
      "    upstream = await fetch(target, init);",
      "    upstream = await fetch(target, init);\n    await fetch(target, init);",
    );
    expect(() => assertThin(mutated)).toThrow("exactly one upstream request");
  });

  it("rejects response-body transformation", () => {
    const mutated = source.replace(
      "  return new Response(upstream.body, {",
      "  const rewritten = await upstream.text();\n  return new Response(rewritten, {",
    );
    expect(() => assertThin(mutated)).toThrow("transport-shaping calls");
  });

  it("rejects expression-level response branching", () => {
    const mutated = source.replace(
      "  return new Response(upstream.body, {",
      '  return new Response(request.nextUrl.pathname.includes("attest") ? "blocked" : upstream.body, {',
    );
    expect(() => assertThin(mutated)).toThrow("ternary proxy branches");
  });
});
