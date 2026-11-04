import {WorkerEntrypoint} from 'cloudflare:workers';
import {openMenuRequest} from '../cloudflare/shared-menu.mjs';
export {default,SeventeenCE} from './worker.mjs';
export class MenuBridge extends WorkerEntrypoint {
  async openMenu(event) {
    return openMenuRequest(this.env,event);
  }
}
