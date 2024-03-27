
from flask import abort, g, request
from flask.typing import ResponseReturnValue
from flask_apispec import MethodResource
from sqlalchemy import String, cast
from sqlalchemy.orm import aliased, joinedload, selectin_polymorphic, selectinload, with_polymorphic

from api.apispec import FlaskApiSpecDecorators
from api.authorization import AuthorizationHelpers
from api.extensions import db
from api.models import (
    AccessRequest,
    AccessRequestStatus,
    App,
    AppGroup,
    OktaGroup,
    OktaGroupTagMap,
    OktaUser,
    OktaUserGroupMember,
    RoleGroup,
    RoleGroupMap,
)
from api.operations import (
    ApproveAccessRequest,
    CreateAccessRequest,
    RejectAccessRequest,
)
from api.operations.constraints import (
    CheckForReason,
)
from api.pagination import paginate
from api.views.schemas import (
    AccessRequestPaginationSchema,
    AccessRequestSchema,
    CreateAccessRequestSchema,
    ResolveAccessRequestSchema,
    SearchAccessRequestPaginationRequestSchema,
)

# Use selectinload for one-to-many eager loading and used joinedload for one-to-one eager loading
ROLE_ASSOCIATED_GROUP_TYPES = with_polymorphic(OktaGroup, [AppGroup,], flat=True)
DEFAULT_LOAD_OPTIONS = (
    joinedload(AccessRequest.requester),
    joinedload(AccessRequest.requested_group).options(
        selectin_polymorphic(OktaGroup, [AppGroup, RoleGroup]),
        joinedload(AppGroup.app),
        selectinload(OktaGroup.active_group_tags).options(
            joinedload(OktaGroupTagMap.active_tag),
            joinedload(OktaGroupTagMap.active_app_tag_mapping)
        ),
        joinedload(RoleGroup.active_role_associated_group_member_mappings).options(
            joinedload(RoleGroupMap.active_group.of_type(ROLE_ASSOCIATED_GROUP_TYPES)).options(
                selectinload(ROLE_ASSOCIATED_GROUP_TYPES.active_group_tags).options(
                    joinedload(OktaGroupTagMap.active_tag),
                    joinedload(OktaGroupTagMap.active_app_tag_mapping)
                ),
                joinedload(ROLE_ASSOCIATED_GROUP_TYPES.AppGroup.app)
            ),
        ),
        joinedload(RoleGroup.active_role_associated_group_owner_mappings).options(
            joinedload(RoleGroupMap.active_group.of_type(ROLE_ASSOCIATED_GROUP_TYPES)).options(
                selectinload(ROLE_ASSOCIATED_GROUP_TYPES.active_group_tags).options(
                    joinedload(OktaGroupTagMap.active_tag),
                    joinedload(OktaGroupTagMap.active_app_tag_mapping)
                ),
                joinedload(ROLE_ASSOCIATED_GROUP_TYPES.AppGroup.app)
            ),
        ),
    ),
    joinedload(AccessRequest.resolver),
)


class AccessRequestResource(MethodResource):
    @FlaskApiSpecDecorators.response_schema(AccessRequestSchema)
    def get(self, access_request_id: str) -> ResponseReturnValue:
        schema = AccessRequestSchema(
            only=(
                "id",
                "created_at",
                "updated_at",
                "resolved_at",
                "status",
                "request_ownership",
                "request_reason",
                "request_ending_at",
                "requester.id",
                "requester.email",
                "requester.first_name",
                "requester.last_name",
                "requester.display_name",
                "requester.deleted_at",
                "requested_group.id",
                "requested_group.type",
                "requested_group.name",
                "requested_group.deleted_at",
                "requested_group.is_owner",
                "requested_group.app.id",
                "requested_group.app.name",
                "requested_group.app",
                "requested_group.active_group_tags",
                "requested_group.active_role_associated_group_member_mappings",
                "requested_group.active_role_associated_group_owner_mappings",
                "resolver.id",
                "resolver.email",
                "resolver.first_name",
                "resolver.last_name",
                "resolver.display_name",
                "resolution_reason",
                "approval_ending_at",
            )
        )
        access_request = (
            AccessRequest.query.options(DEFAULT_LOAD_OPTIONS)
            .filter(AccessRequest.id == access_request_id)
            .first_or_404()
        )
        return schema.dump(access_request)

    @FlaskApiSpecDecorators.request_schema(ResolveAccessRequestSchema)
    @FlaskApiSpecDecorators.response_schema(AccessRequestSchema)
    def put(self, access_request_id: str) -> ResponseReturnValue:
        access_request = (
            AccessRequest.query.options(
                joinedload(AccessRequest.active_requested_group)
            )
            .filter(AccessRequest.id == access_request_id)
            .first_or_404()
        )

        access_request_args = ResolveAccessRequestSchema().load(request.get_json())

        if access_request_args["approved"]:
            # Check if the current user is the user who created the request (they can always reject their own requests)
            if access_request.requester_user_id == g.current_user_id:
                    abort(403, "Users cannot approve their own requests")


            # Check group tags to see if a reason is required for approval
            valid, err_message = CheckForReason(
                group=access_request.active_requested_group,
                reason=access_request_args.get("reason"),
                members_to_add=[access_request.requester_user_id] if not access_request.request_ownership else [],
                owners_to_add=[access_request.requester_user_id] if access_request.request_ownership else []
            ).execute_for_group()
            if not valid:
                abort(400, err_message)


        # Otherwise check if the current user can manage the requested group for the access request
        elif not AuthorizationHelpers.can_manage_group(
            access_request.active_requested_group
        ):
            abort(403, "Current user is not allowed to perform this action")

        if (
            access_request.status != AccessRequestStatus.PENDING
            or access_request.resolved_at is not None
        ):
            abort(400, "Access request is not pending")

        if access_request_args["approved"]:
            if not access_request.requested_group.is_managed:
                abort(
                    400,
                    "Groups not managed by Access cannot be modified",
                )
            ApproveAccessRequest(
                access_request=access_request,
                approver_user=g.current_user_id,
                approval_reason=access_request_args.get("reason"),
                ending_at=access_request_args.get("ending_at"),
            ).execute()
        else:
            RejectAccessRequest(
                access_request=access_request,
                rejection_reason=access_request_args.get("reason"),
                notify_requester=access_request.requester_user_id != g.current_user_id,
                current_user_id=g.current_user_id
            ).execute()

        access_request = (
            AccessRequest.query.options(DEFAULT_LOAD_OPTIONS)
            .filter(AccessRequest.id == access_request.id)
            .first()
        )
        return AccessRequestSchema(
            only=(
                "id",
                "created_at",
                "updated_at",
                "resolved_at",
                "status",
                "request_ownership",
                "request_reason",
                "request_ending_at",
                "requester.id",
                "requester.email",
                "requester.first_name",
                "requester.last_name",
                "requester.display_name",
                "requester.deleted_at",
                "requested_group.id",
                "requested_group.type",
                "requested_group.name",
                "requested_group.deleted_at",
                "resolver.id",
                "resolver.email",
                "resolver.first_name",
                "resolver.last_name",
                "resolver.display_name",
                "resolution_reason",
            )
        ).dump(access_request)


class AccessRequestList(MethodResource):
    @FlaskApiSpecDecorators.request_schema(
        SearchAccessRequestPaginationRequestSchema, location="query"
    )
    @FlaskApiSpecDecorators.response_schema(AccessRequestPaginationSchema)
    def get(self) -> ResponseReturnValue:
        search_args = SearchAccessRequestPaginationRequestSchema().load(request.args)

        query = AccessRequest.query.options(DEFAULT_LOAD_OPTIONS).order_by(
            AccessRequest.created_at.desc()
        )

        if "status" in search_args:
            query = query.filter(AccessRequest.status == search_args["status"])

        if "requester_user_id" in search_args:
            if search_args["requester_user_id"] == "@me":
                query = query.filter(
                    AccessRequest.requester_user_id == g.current_user_id
                )
            else:
                requester_alias = aliased(OktaUser)
                query = query.join(AccessRequest.requester.of_type(requester_alias)).filter(
                    db.or_(
                        AccessRequest.requester_user_id
                        == search_args["requester_user_id"],
                        requester_alias.email.ilike(
                            search_args["requester_user_id"]
                        ),
                    )
                )

        if "requested_group_id" in search_args:
            query = query.join(AccessRequest.requested_group).filter(
                db.or_(
                    AccessRequest.requested_group_id
                    == search_args["requested_group_id"],
                    OktaGroup.name.ilike(search_args["requested_group_id"]),
                )
            )

        if "assignee_user_id" in search_args:
            assignee_user_id = search_args["assignee_user_id"]
            if search_args["assignee_user_id"] == "@me":
                assignee_user_id = g.current_user_id

            assignee_user = OktaUser.query.filter(
                db.or_(
                    OktaUser.id == assignee_user_id,
                    OktaUser.email.ilike(assignee_user_id),
                )
            ).first()

            if assignee_user is not None:
                groups_owned_subquery = (
                    db.session.query(OktaGroup.id)
                    .options(selectinload(OktaGroup.active_user_ownerships))
                    .join(OktaGroup.active_user_ownerships)
                    .filter(OktaGroup.deleted_at.is_(None))
                    .filter(OktaUserGroupMember.user_id == assignee_user.id)
                    .subquery()
                )
                owner_app_group_alias = aliased(AppGroup)
                app_groups_owned_subquery = (
                    db.session.query(AppGroup.id)
                    .options(
                        joinedload(AppGroup.app)
                        .joinedload(App.active_owner_app_groups.of_type(owner_app_group_alias))
                        .selectinload(owner_app_group_alias.active_user_ownerships)
                    )
                    .join(AppGroup.app)
                    .join(App.active_owner_app_groups.of_type(owner_app_group_alias))
                    .join(owner_app_group_alias.active_user_ownerships)
                    .filter(AppGroup.deleted_at.is_(None))
                    .filter(OktaUserGroupMember.user_id == assignee_user.id)
                    .subquery()
                )

                query = query.join(AccessRequest.requested_group).filter(
                    db.or_(
                        OktaGroup.id.in_(groups_owned_subquery),
                        OktaGroup.id.in_(app_groups_owned_subquery),
                    )
                )
            else:
                query = query.filter(False)

        if "resolver_user_id" in search_args:
            if search_args["resolver_user_id"] == "@me":
                query = query.filter(
                    AccessRequest.resolver_user_id == g.current_user_id
                )
            else:
                resolver_alias = aliased(OktaUser)
                query = query.outerjoin(AccessRequest.resolver.of_type(resolver_alias)).filter(
                    db.or_(
                        AccessRequest.resolver_user_id
                        == search_args["resolver_user_id"],
                        resolver_alias.email.ilike(search_args["resolver_user_id"]),
                    )
                )

        # Implement basic search with the "q" url parameter
        if "q" in search_args and len(search_args["q"]) > 0:
            like_search = f"%{search_args['q']}%"
            requester_alias = aliased(OktaUser)
            resolver_alias = aliased(OktaUser)
            query = (
                query.join(AccessRequest.requester.of_type(requester_alias))
                .join(AccessRequest.requested_group)
                .outerjoin(AccessRequest.resolver.of_type(resolver_alias))
                .filter(
                    db.or_(
                        AccessRequest.id.like(f"{search_args['q']}%"),
                        cast(AccessRequest.status, String).ilike(like_search),
                        requester_alias.email.ilike(like_search),
                        requester_alias.first_name.ilike(like_search),
                        requester_alias.last_name.ilike(like_search),
                        requester_alias.display_name.ilike(like_search),
                        (
                            requester_alias.first_name + " " + requester_alias.last_name
                        ).ilike(like_search),
                        OktaGroup.name.ilike(like_search),
                        OktaGroup.description.ilike(like_search),
                        resolver_alias.email.ilike(like_search),
                        resolver_alias.first_name.ilike(like_search),
                        resolver_alias.last_name.ilike(like_search),
                        resolver_alias.display_name.ilike(like_search),
                        (
                            resolver_alias.first_name + " " + resolver_alias.last_name
                        ).ilike(like_search),
                    )
                )
            )

        return paginate(
            query,
            AccessRequestSchema(
                many=True,
                only=(
                    "id",
                    "created_at",
                    "updated_at",
                    "resolved_at",
                    "status",
                    "request_ownership",
                    "requester.id",
                    "requester.email",
                    "requester.first_name",
                    "requester.last_name",
                    "requester.display_name",
                    "requester.deleted_at",
                    "requested_group.id",
                    "requested_group.type",
                    "requested_group.name",
                    "requested_group.deleted_at",
                    "resolver.id",
                    "resolver.email",
                    "resolver.first_name",
                    "resolver.last_name",
                    "resolver.display_name",
                ),
            ),
        )

    @FlaskApiSpecDecorators.request_schema(CreateAccessRequestSchema)
    @FlaskApiSpecDecorators.response_schema(AccessRequestSchema)
    def post(self) -> ResponseReturnValue:
        access_request_args = CreateAccessRequestSchema().load(request.get_json())

        if (
            OktaUser.query
            .filter(OktaUser.deleted_at.is_(None))
            .filter(OktaUser.id == g.current_user_id).first() is None
        ):
            abort(403, "Current user is not allowed to perform this action")

        group = (
            db.session.query(with_polymorphic(OktaGroup, [AppGroup, RoleGroup]))
            .filter(OktaGroup.deleted_at.is_(None))
            .filter(OktaGroup.id == access_request_args["group_id"])
            .first_or_404()
        )

        if not group.is_managed:
            abort(
                400,
                "Groups not managed by Access cannot be modified",
            )

        existing_access_requests = (
            AccessRequest.query.filter(
                AccessRequest.requester_user_id == g.current_user_id
            )
            .filter(
                AccessRequest.requested_group_id == access_request_args["group_id"]
            )
            .filter(
                AccessRequest.request_ownership
                == access_request_args["group_owner"]
            )
            .filter(AccessRequest.status == AccessRequestStatus.PENDING)
            .filter(AccessRequest.resolved_at.is_(None))
            .all()
        )
        for existing_access_request in existing_access_requests:
            RejectAccessRequest(
                access_request=existing_access_request,
                rejection_reason="Closed due to duplicate access request creation",
                notify_requester=False,
                current_user_id=g.current_user_id
            ).execute()

        access_request = CreateAccessRequest(
            requester_user=g.current_user_id,
            requested_group=access_request_args["group_id"],
            request_ownership=access_request_args["group_owner"],
            request_reason=access_request_args.get("reason"),
            request_ending_at=access_request_args.get("ending_at"),
        ).execute()

        if access_request is None:
            abort(400, "Groups not managed by Access cannot be modified")

        access_request = (
            AccessRequest.query.options(DEFAULT_LOAD_OPTIONS)
            .filter(AccessRequest.id == access_request.id)
            .first()
        )
        return (
            AccessRequestSchema(
                only=(
                    "id",
                    "created_at",
                    "updated_at",
                    "resolved_at",
                    "status",
                    "request_ownership",
                    "request_reason",
                    "request_ending_at",
                    "requester.id",
                    "requester.email",
                    "requester.first_name",
                    "requester.last_name",
                    "requester.display_name",
                    "requester.deleted_at",
                    "requested_group.id",
                    "requested_group.type",
                    "requested_group.name",
                    "requested_group.deleted_at",
                ),
            ).dump(access_request),
            201,
        )
