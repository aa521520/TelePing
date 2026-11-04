import {provinces} from './regions.mjs';

const operators = {1:'电信',2:'联通',3:'国外',4:'其他',6:'铁通',7:'移动',8:'教育网',10:'科技网',11:'华数',13:'鹏博士',14:'有线通',15:'广电',16:'视讯宽带',17:'香港',18:'台湾',19:'澳门'};
const areas = {1:'大陆',2:'港澳台',3:'国外'};
const nodes = {1:'IDC',2:'路由器',3:'桌面',4:'手机 Wi-Fi',5:'手机 4G'};

export function operatorName(id) {
  return operators[id] || '未知运营商';
}

export function channelText(profile = {}) {
  const area = areas[profile.area] || '未知地区';
  const selected = Array.isArray(profile.provinces) ? profile.provinces : [];
  const names = selected.map(id => provinces.find(p => p.id === id && p.area === profile.area)?.name || '未知省份');
  const region = names.length ? `${area}（${names.join('、')}）` : `${area}（全地区）`;
  const node = nodes[profile.node_type] || '未知节点';
  const count = [1,2,3].includes(profile.nodes_per_region) ? profile.nodes_per_region : '未知';
  return `地区：${region}｜节点类型：${node}｜每区域节点数：${count}`;
}
