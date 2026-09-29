import { existsSync, lstatSync, readlinkSync, symlinkSync, unlinkSync } from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const dashboardModules = path.resolve(here, '../../experience-dashboard/node_modules');
const demoModules = path.resolve(here, '../node_modules');
const dashboardPublic = path.resolve(here, '../../experience-dashboard/public');
const demoPublic = path.resolve(here, '../public');
const relativeTarget = path.relative(path.dirname(dashboardModules), demoModules);
let removeLinkAfterCommand = false;
let removePublicLinkAfterCommand = false;

if (existsSync(dashboardModules)) {
  const stat = lstatSync(dashboardModules);
  if (stat.isSymbolicLink()) {
    const currentTarget = path.resolve(path.dirname(dashboardModules), readlinkSync(dashboardModules));
    removeLinkAfterCommand = currentTarget === demoModules;
  }
} else {
  symlinkSync(relativeTarget, dashboardModules, 'dir');
  removeLinkAfterCommand = true;
}

if (existsSync(demoPublic)) {
  const stat = lstatSync(demoPublic);
  if (stat.isSymbolicLink()) {
    const currentTarget = path.resolve(path.dirname(demoPublic), readlinkSync(demoPublic));
    removePublicLinkAfterCommand = currentTarget === dashboardPublic;
  }
} else {
  symlinkSync(dashboardPublic, demoPublic, 'dir');
  removePublicLinkAfterCommand = true;
}

const [command, ...args] = process.argv.slice(2);
if (!command) throw new Error('shared UI build command is required');
let result;
try {
  result = spawnSync(command, args, {
    cwd: path.resolve(here, '..'),
    env: { ...process.env, PATH: `${path.join(demoModules, '.bin')}${path.delimiter}${process.env.PATH ?? ''}` },
    stdio: 'inherit',
  });
} finally {
  if (removeLinkAfterCommand && lstatSync(dashboardModules, { throwIfNoEntry: false })?.isSymbolicLink()) {
    const currentTarget = path.resolve(path.dirname(dashboardModules), readlinkSync(dashboardModules));
    if (currentTarget === demoModules) unlinkSync(dashboardModules);
  }
  if (removePublicLinkAfterCommand && lstatSync(demoPublic, { throwIfNoEntry: false })?.isSymbolicLink()) {
    const currentTarget = path.resolve(path.dirname(demoPublic), readlinkSync(demoPublic));
    if (currentTarget === dashboardPublic) unlinkSync(demoPublic);
  }
}

if (result.error) throw result.error;
process.exit(result.status ?? 1);
