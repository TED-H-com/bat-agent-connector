// @ts-nocheck
import {h} from '../../../src/ui/dom.js';
import {t} from '../../../src/i18n.js';
import {mountTailscale} from '../../../src/tailscale.js';

mountTailscale(document.querySelector('main'), {h, t});
