const CHINA_REGION_NAMES = Object.freeze({
  'CN-11':'北京市','CN-12':'天津市','CN-13':'河北省','CN-14':'山西省','CN-15':'内蒙古自治区',
  'CN-21':'辽宁省','CN-22':'吉林省','CN-23':'黑龙江省','CN-31':'上海市','CN-32':'江苏省',
  'CN-33':'浙江省','CN-34':'安徽省','CN-35':'福建省','CN-36':'江西省','CN-37':'山东省',
  'CN-41':'河南省','CN-42':'湖北省','CN-43':'湖南省','CN-44':'广东省','CN-45':'广西壮族自治区',
  'CN-46':'海南省','CN-50':'重庆市','CN-51':'四川省','CN-52':'贵州省','CN-53':'云南省',
  'CN-54':'西藏自治区','CN-61':'陕西省','CN-62':'甘肃省','CN-63':'青海省','CN-64':'宁夏回族自治区',
  'CN-65':'新疆维吾尔自治区','CN-71':'台湾省','CN-81':'香港特别行政区','CN-82':'澳门特别行政区',
});

export function regionDisplayName(code) {
  const value=String(code||'').trim();
  return value ? (CHINA_REGION_NAMES[value] || value) : '—';
}

export function regionDisplayWithCode(code) {
  const value=String(code||'').trim();if(!value)return '—';
  const name=regionDisplayName(value);return name===value?value:`${name} · ${value}`;
}

export { CHINA_REGION_NAMES };
