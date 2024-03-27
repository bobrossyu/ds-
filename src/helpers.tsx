import {PolymorphicGroup, OktaUser, Tag, OktaGroupTagMap} from './api/apiSchemas';

export function displayGroupType(group: PolymorphicGroup | undefined) {
  if (group == undefined || group.type == undefined) {
    return '';
  }
  if (group.type == 'okta_group') {
    return 'Group';
  }
  if (group.type == 'role_group') {
    return 'Role';
  }
  return group.type
    .split('_')
    .map((word) => word[0].toUpperCase() + word.substring(1))
    .join(' ');
}

export function displayUserName(user: OktaUser | undefined) {
  if (user == undefined) {
    return '';
  }
  return user.display_name != null ? user.display_name : user.first_name + ' ' + user.last_name;
}

// https://stackoverflow.com/a/8817461
export function deepFind(obj: any, path: string) {
  var paths = path.split('.'),
    current = obj,
    i;

  for (i = 0; i < paths.length; ++i) {
    if (current[paths[i]] == undefined) {
      return undefined;
    } else {
      current = current[paths[i]];
    }
  }
  return current;
}

// https://stackoverflow.com/a/34890276
export function groupBy(xs: Array<any>, key: string) {
  return xs.reduce(function (rv, x) {
    const newKey = deepFind(x, key);
    (rv[newKey] = rv[newKey] || []).push(x);
    return rv;
  }, {});
}

export function getActiveTagsFromGroups(groups: PolymorphicGroup[]) {
  return Array.from(
    groups.reduce((allTags, curr) => {
      if (curr.active_group_tags) {
        const groupTags = curr.active_group_tags.map((t: OktaGroupTagMap) => t.active_tag!);
        groupTags.forEach((item) => allTags.add(item));
        return allTags;
      } else return allTags;
    }, new Set<Tag>()),
  );
}

// returns true if targetTag is set to true at least once in the tag list
function checkBooleanTag(tags: Tag[] | undefined, targetTag: string) {
  if (!tags) return false;

  return tags.reduce((out: boolean, curr: Tag) => {
    if (curr.enabled && curr.constraints && Object.keys(curr.constraints).includes(targetTag)) {
      return out || curr.constraints![targetTag];
    } else return out;
  }, false);
}

export function minTagTime(tags: Tag[], owner: boolean) {
  if (owner) {
    const timeLimited = tags.filter(
      (tag: Tag) => tag.enabled && tag.constraints && Object.keys(tag.constraints).includes('owner_time_limit'),
    );
    return timeLimited.length > 0
      ? timeLimited.reduce((prev, curr) => {
          return prev < curr.constraints!['owner_time_limit'] ? prev : curr.constraints!['owner_time_limit'];
        }, Number.MAX_VALUE)
      : null;
  } else {
    const timeLimited = tags.filter(
      (tag: Tag) => tag.enabled && tag.constraints && Object.keys(tag.constraints).includes('member_time_limit'),
    );
    return timeLimited.length > 0
      ? timeLimited.reduce((prev, curr) => {
          return prev < curr.constraints!['member_time_limit'] ? prev : curr.constraints!['member_time_limit'];
        }, Number.MAX_VALUE)
      : null;
  }
}

export function minTagTimeGroups(groups: PolymorphicGroup[], owner: boolean) {
  return minTagTime(getActiveTagsFromGroups(groups), owner);
}

export function requiredReason(tags: Tag[] | undefined, owner: boolean) {
  if (!tags) return false;

  return owner ? checkBooleanTag(tags, 'require_owner_reason') : checkBooleanTag(tags, 'require_member_reason');
}

export function requiredReasonGroups(groups: PolymorphicGroup[], owner: boolean) {
  return requiredReason(getActiveTagsFromGroups(groups), owner);
}

export function ownerCantAddSelf(tags: Tag[] | undefined, owner: boolean) {
  if (!tags) return false;

  return owner
    ? checkBooleanTag(tags, 'disallow_self_add_ownership')
    : checkBooleanTag(tags, 'disallow_self_add_membership');
}

export function ownerCantAddSelfGroups(groups: PolymorphicGroup[], owner: boolean) {
  return ownerCantAddSelf(getActiveTagsFromGroups(groups), owner);
}
