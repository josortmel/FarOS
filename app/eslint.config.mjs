// #1361 (20-nov-2026): el lint de la app. Reglas recomendadas de eslint, sin estilo (el estilo es de prettier).
// Acordado con Lienzo, dueño de la app: los parámetros sin usar se permiten con «_» delante, y en
// renderer/js/api.js todos, porque el modo simulado copia a propósito la firma de la API real.
import js from '@eslint/js';
import globals from 'globals';

export default [
  { ignores: ['node_modules/**', 'dist/**', 'assets/**'] },
  js.configs.recommended,
  {
    rules: {
      // ignoreRestSiblings: «const { contenido, ...fila } = it» es la forma de quitar un campo, no un olvido.
      'no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', caughtErrorsIgnorePattern: '^_', ignoreRestSiblings: true },
      ],
    },
  },
  // La interfaz: módulos del navegador.
  { files: ['renderer/**/*.js'], languageOptions: { sourceType: 'module', globals: { ...globals.browser } } },
  // El proceso principal de Electron y sus tests: Node con require.
  { files: ['*.js', 'test/**/*.js'], languageOptions: { sourceType: 'commonjs', globals: { ...globals.node } } },
  // Scripts .mjs (el puente del móvil y el proxy de desarrollo): Node con import.
  { files: ['**/*.mjs'], languageOptions: { sourceType: 'module', globals: { ...globals.node } } },
  {
    files: ['renderer/js/api.js'],
    rules: { 'no-unused-vars': ['error', { args: 'none', caughtErrorsIgnorePattern: '^_', ignoreRestSiblings: true }] },
  },
];
