import {WorkerEntrypoint} from 'cloudflare:workers';
import worker, {TelePing} from './worker.mjs';
import {openMenuRequest} from './shared-menu.mjs';
export default worker;
export {TelePing};
export class MenuBridge extends WorkerEntrypoint { async openMenu(event){return openMenuRequest(this.env,event)} }
