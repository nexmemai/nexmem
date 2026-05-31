/**
 * Jest config for nexmem-js (P12-J2, Block 8).
 *
 * Uses ts-jest to compile TypeScript test files in-flight. Tests live in
 * src/__tests__/ alongside the sources so editor jump-to-definition works
 * without a separate root.
 *
 * Note on ESM: package.json sets "type": "module", but ts-jest historically
 * requires CommonJS for the transformer to work without flags. We run the
 * tests as CJS by overriding moduleFileExtensions and pinning ts-jest's
 * useESM=false (the default). The shipped library output is still ESM —
 * this only affects how Jest invokes ts-jest at test time.
 */
export default {
  preset: "ts-jest",
  testEnvironment: "node",
  roots: ["<rootDir>/src"],
  testMatch: ["**/__tests__/**/*.test.ts"],
  moduleFileExtensions: ["ts", "tsx", "js"],
  // Strip the .js extension that the source files use for ESM-style imports
  // ("./client.js"). Without this, ts-jest in CJS mode cannot resolve them.
  moduleNameMapper: {
    "^(\\.{1,2}/.*)\\.js$": "$1",
  },
  transform: {
    "^.+\\.ts$": [
      "ts-jest",
      {
        // Match the source tsconfig so imports resolve identically.
        tsconfig: "tsconfig.json",
        useESM: false,
        diagnostics: {
          // Skip the "import.meta" warning ts-jest emits for module: NodeNext.
          ignoreCodes: [1343],
        },
      },
    ],
  },
  // We don't want to ship the test output to npm — there is a separate
  // .npmignore-via-files allowlist in package.json that limits the
  // package to "dist" and "README.md".
  collectCoverage: false,
};
