# Box2D API Reference <a name="box2d-api"></a>
> Source: Box2D GitHub | Generated: 2026-04-30
> Focus: Fixture creation, mass data recalculation, world stepping

## Table of Contents
- [b2World](#b2world)
- [b2Body](#b2body)
- [b2Shape](#b2shape)
- [b2MassData](#b2massdata)
- [b2Vec2](#b2vec2)


---
### b2World <a name="b2world"></a>
```c
bool b2World_IsValid( b2WorldId id );
void b2World_Step( b2WorldId worldId, float timeStep, int subStepCount );
void b2World_Draw( b2WorldId worldId, b2DebugDraw* draw );
b2BodyEvents b2World_GetBodyEvents( b2WorldId worldId );
b2SensorEvents b2World_GetSensorEvents( b2WorldId worldId );
b2ContactEvents b2World_GetContactEvents( b2WorldId worldId );
b2JointEvents b2World_GetJointEvents( b2WorldId worldId );
b2TreeStats b2World_OverlapAABB( b2WorldId worldId, b2AABB aabb, b2QueryFilter filter, b2OverlapResultFcn* fcn,
b2TreeStats b2World_OverlapShape( b2WorldId worldId, const b2ShapeProxy* proxy, b2QueryFilter filter,
b2TreeStats b2World_CastRay( b2WorldId worldId, b2Vec2 origin, b2Vec2 translation, b2QueryFilter filter,
b2RayResult b2World_CastRayClosest( b2WorldId worldId, b2Vec2 origin, b2Vec2 translation, b2QueryFilter filter );
b2TreeStats b2World_CastShape( b2WorldId worldId, const b2ShapeProxy* proxy, b2Vec2 translation, b2QueryFilter filter,
float b2World_CastMover( b2WorldId worldId, const b2Capsule* mover, b2Vec2 translation, b2QueryFilter filter );
void b2World_CollideMover( b2WorldId worldId, const b2Capsule* mover, b2QueryFilter filter, b2PlaneResultFcn* fcn,
void b2World_EnableSleeping( b2WorldId worldId, bool flag );
bool b2World_IsSleepingEnabled( b2WorldId worldId );
void b2World_EnableContinuous( b2WorldId worldId, bool flag );
bool b2World_IsContinuousEnabled( b2WorldId worldId );
void b2World_SetRestitutionThreshold( b2WorldId worldId, float value );
float b2World_GetRestitutionThreshold( b2WorldId worldId );
void b2World_SetHitEventThreshold( b2WorldId worldId, float value );
float b2World_GetHitEventThreshold( b2WorldId worldId );
void b2World_SetCustomFilterCallback( b2WorldId worldId, b2CustomFilterFcn* fcn, void* context );
void b2World_SetPreSolveCallback( b2WorldId worldId, b2PreSolveFcn* fcn, void* context );
void b2World_SetGravity( b2WorldId worldId, b2Vec2 gravity );
b2Vec2 b2World_GetGravity( b2WorldId worldId );
void b2World_Explode( b2WorldId worldId, const b2ExplosionDef* explosionDef );
void b2World_SetContactTuning( b2WorldId worldId, float hertz, float dampingRatio, float pushSpeed );
void b2World_SetContactRecycleDistance( b2WorldId worldId, float recycleDistance );
float b2World_GetContactRecycleDistance( b2WorldId worldId );
```


---
### b2Body <a name="b2body"></a>
```c
bool b2Body_IsValid( b2BodyId id );
b2BodyType b2Body_GetType( b2BodyId bodyId );
void b2Body_SetType( b2BodyId bodyId, b2BodyType type );
void b2Body_SetName( b2BodyId bodyId, const char* name );
const char* b2Body_GetName( b2BodyId bodyId );
void b2Body_SetUserData( b2BodyId bodyId, void* userData );
void* b2Body_GetUserData( b2BodyId bodyId );
b2Vec2 b2Body_GetPosition( b2BodyId bodyId );
b2Rot b2Body_GetRotation( b2BodyId bodyId );
b2Transform b2Body_GetTransform( b2BodyId bodyId );
void b2Body_SetTransform( b2BodyId bodyId, b2Vec2 position, b2Rot rotation );
b2Vec2 b2Body_GetLocalPoint( b2BodyId bodyId, b2Vec2 worldPoint );
b2Vec2 b2Body_GetWorldPoint( b2BodyId bodyId, b2Vec2 localPoint );
b2Vec2 b2Body_GetLocalVector( b2BodyId bodyId, b2Vec2 worldVector );
b2Vec2 b2Body_GetWorldVector( b2BodyId bodyId, b2Vec2 localVector );
b2Vec2 b2Body_GetLinearVelocity( b2BodyId bodyId );
float b2Body_GetAngularVelocity( b2BodyId bodyId );
void b2Body_SetLinearVelocity( b2BodyId bodyId, b2Vec2 linearVelocity );
void b2Body_SetAngularVelocity( b2BodyId bodyId, float angularVelocity );
void b2Body_SetTargetTransform( b2BodyId bodyId, b2Transform target, float timeStep, bool wake );
b2Vec2 b2Body_GetLocalPointVelocity( b2BodyId bodyId, b2Vec2 localPoint );
b2Vec2 b2Body_GetWorldPointVelocity( b2BodyId bodyId, b2Vec2 worldPoint );
void b2Body_ApplyForce( b2BodyId bodyId, b2Vec2 force, b2Vec2 point, bool wake );
void b2Body_ApplyForceToCenter( b2BodyId bodyId, b2Vec2 force, bool wake );
void b2Body_ApplyTorque( b2BodyId bodyId, float torque, bool wake );
void b2Body_ClearForces( b2BodyId bodyId );
void b2Body_ApplyLinearImpulse( b2BodyId bodyId, b2Vec2 impulse, b2Vec2 point, bool wake );
void b2Body_ApplyLinearImpulseToCenter( b2BodyId bodyId, b2Vec2 impulse, bool wake );
void b2Body_ApplyAngularImpulse( b2BodyId bodyId, float impulse, bool wake );
float b2Body_GetMass( b2BodyId bodyId );
```


---
### b2Shape <a name="b2shape"></a>
```c
bool b2Shape_IsValid( b2ShapeId id );
b2ShapeType b2Shape_GetType( b2ShapeId shapeId );
b2BodyId b2Shape_GetBody( b2ShapeId shapeId );
b2WorldId b2Shape_GetWorld( b2ShapeId shapeId );
bool b2Shape_IsSensor( b2ShapeId shapeId );
void b2Shape_SetUserData( b2ShapeId shapeId, void* userData );
void* b2Shape_GetUserData( b2ShapeId shapeId );
void b2Shape_SetDensity( b2ShapeId shapeId, float density, bool updateBodyMass );
float b2Shape_GetDensity( b2ShapeId shapeId );
void b2Shape_SetFriction( b2ShapeId shapeId, float friction );
float b2Shape_GetFriction( b2ShapeId shapeId );
void b2Shape_SetRestitution( b2ShapeId shapeId, float restitution );
float b2Shape_GetRestitution( b2ShapeId shapeId );
void b2Shape_SetUserMaterial( b2ShapeId shapeId, uint64_t material );
uint64_t b2Shape_GetUserMaterial( b2ShapeId shapeId );
void b2Shape_SetSurfaceMaterial( b2ShapeId shapeId, const b2SurfaceMaterial* surfaceMaterial );
b2SurfaceMaterial b2Shape_GetSurfaceMaterial( b2ShapeId shapeId );
b2Filter b2Shape_GetFilter( b2ShapeId shapeId );
void b2Shape_SetFilter( b2ShapeId shapeId, b2Filter filter );
void b2Shape_EnableSensorEvents( b2ShapeId shapeId, bool flag );
bool b2Shape_AreSensorEventsEnabled( b2ShapeId shapeId );
void b2Shape_EnableContactEvents( b2ShapeId shapeId, bool flag );
bool b2Shape_AreContactEventsEnabled( b2ShapeId shapeId );
void b2Shape_EnablePreSolveEvents( b2ShapeId shapeId, bool flag );
bool b2Shape_ArePreSolveEventsEnabled( b2ShapeId shapeId );
void b2Shape_EnableHitEvents( b2ShapeId shapeId, bool flag );
bool b2Shape_AreHitEventsEnabled( b2ShapeId shapeId );
bool b2Shape_TestPoint( b2ShapeId shapeId, b2Vec2 point );
b2CastOutput b2Shape_RayCast( b2ShapeId shapeId, const b2RayCastInput* input );
b2Circle b2Shape_GetCircle( b2ShapeId shapeId );
```


---
### b2 <a name="b2"></a>

 * @defgroup world World * These functions allow you to create a simulation world. * * You can add rigid bodies and joint constraints to the world and run the simulation. You can get contact * information to get contact points and normals as well as events. You can query to world, checking for overlaps and casting rays * or shapes. There is also debugging information such as debug draw, timing information, and counters. You can find documentation * here: https://box2d.org/ * @{ 

```c
b2WorldId b2CreateWorld( const b2WorldDef* def );
void b2DestroyWorld( b2WorldId worldId );
b2BodyId b2CreateBody( b2WorldId worldId, const b2BodyDef* def );
void b2DestroyBody( b2BodyId bodyId );
b2ShapeId b2CreateCircleShape( b2BodyId bodyId, const b2ShapeDef* def, const b2Circle* circle );
b2ShapeId b2CreateSegmentShape( b2BodyId bodyId, const b2ShapeDef* def, const b2Segment* segment );
b2ShapeId b2CreateCapsuleShape( b2BodyId bodyId, const b2ShapeDef* def, const b2Capsule* capsule );
b2ShapeId b2CreatePolygonShape( b2BodyId bodyId, const b2ShapeDef* def, const b2Polygon* polygon );
void b2DestroyShape( b2ShapeId shapeId, bool updateBodyMass );
b2ChainId b2CreateChain( b2BodyId bodyId, const b2ChainDef* def );
void b2DestroyChain( b2ChainId chainId );
void b2DestroyJoint( b2JointId jointId, bool wakeAttached );
b2JointId b2CreateDistanceJoint( b2WorldId worldId, const b2DistanceJointDef* def );
b2JointId b2CreateMotorJoint( b2WorldId worldId, const b2MotorJointDef* def );
b2JointId b2CreateFilterJoint( b2WorldId worldId, const b2FilterJointDef* def );
b2JointId b2CreatePrismaticJoint( b2WorldId worldId, const b2PrismaticJointDef* def );
b2JointId b2CreateRevoluteJoint( b2WorldId worldId, const b2RevoluteJointDef* def );
b2JointId b2CreateWeldJoint( b2WorldId worldId, const b2WeldJointDef* def );
b2JointId b2CreateWheelJoint( b2WorldId worldId, const b2WheelJointDef* def );
```


---
### b2Chain <a name="b2chain"></a>
```c
b2WorldId b2Chain_GetWorld( b2ChainId chainId );
int b2Chain_GetSegmentCount( b2ChainId chainId );
int b2Chain_GetSegments( b2ChainId chainId, b2ShapeId* segmentArray, int capacity );
int b2Chain_GetSurfaceMaterialCount( b2ChainId chainId );
void b2Chain_SetSurfaceMaterial( b2ChainId chainId, const b2SurfaceMaterial* material, int materialIndex );
b2SurfaceMaterial b2Chain_GetSurfaceMaterial( b2ChainId chainId, int materialIndex );
bool b2Chain_IsValid( b2ChainId id );
```


---
### b2Joint <a name="b2joint"></a>
```c
bool b2Joint_IsValid( b2JointId id );
b2JointType b2Joint_GetType( b2JointId jointId );
b2BodyId b2Joint_GetBodyA( b2JointId jointId );
b2BodyId b2Joint_GetBodyB( b2JointId jointId );
b2WorldId b2Joint_GetWorld( b2JointId jointId );
void b2Joint_SetLocalFrameA( b2JointId jointId, b2Transform localFrame );
b2Transform b2Joint_GetLocalFrameA( b2JointId jointId );
void b2Joint_SetLocalFrameB( b2JointId jointId, b2Transform localFrame );
b2Transform b2Joint_GetLocalFrameB( b2JointId jointId );
void b2Joint_SetCollideConnected( b2JointId jointId, bool shouldCollide );
bool b2Joint_GetCollideConnected( b2JointId jointId );
void b2Joint_SetUserData( b2JointId jointId, void* userData );
void* b2Joint_GetUserData( b2JointId jointId );
void b2Joint_WakeBodies( b2JointId jointId );
b2Vec2 b2Joint_GetConstraintForce( b2JointId jointId );
float b2Joint_GetConstraintTorque( b2JointId jointId );
float b2Joint_GetLinearSeparation( b2JointId jointId );
float b2Joint_GetAngularSeparation( b2JointId jointId );
void b2Joint_SetConstraintTuning( b2JointId jointId, float hertz, float dampingRatio );
void b2Joint_GetConstraintTuning( b2JointId jointId, float* hertz, float* dampingRatio );
void b2Joint_SetForceThreshold( b2JointId jointId, float threshold );
float b2Joint_GetForceThreshold( b2JointId jointId );
void b2Joint_SetTorqueThreshold( b2JointId jointId, float threshold );
float b2Joint_GetTorqueThreshold( b2JointId jointId );
```


---
### b2DistanceJoint <a name="b2distancejoint"></a>
```c
void b2DistanceJoint_SetLength( b2JointId jointId, float length );
float b2DistanceJoint_GetLength( b2JointId jointId );
void b2DistanceJoint_EnableSpring( b2JointId jointId, bool enableSpring );
bool b2DistanceJoint_IsSpringEnabled( b2JointId jointId );
void b2DistanceJoint_SetSpringForceRange( b2JointId jointId, float lowerForce, float upperForce );
void b2DistanceJoint_GetSpringForceRange( b2JointId jointId, float* lowerForce, float* upperForce );
void b2DistanceJoint_SetSpringHertz( b2JointId jointId, float hertz );
void b2DistanceJoint_SetSpringDampingRatio( b2JointId jointId, float dampingRatio );
float b2DistanceJoint_GetSpringHertz( b2JointId jointId );
float b2DistanceJoint_GetSpringDampingRatio( b2JointId jointId );
void b2DistanceJoint_EnableLimit( b2JointId jointId, bool enableLimit );
bool b2DistanceJoint_IsLimitEnabled( b2JointId jointId );
void b2DistanceJoint_SetLengthRange( b2JointId jointId, float minLength, float maxLength );
float b2DistanceJoint_GetMinLength( b2JointId jointId );
float b2DistanceJoint_GetMaxLength( b2JointId jointId );
float b2DistanceJoint_GetCurrentLength( b2JointId jointId );
void b2DistanceJoint_EnableMotor( b2JointId jointId, bool enableMotor );
bool b2DistanceJoint_IsMotorEnabled( b2JointId jointId );
void b2DistanceJoint_SetMotorSpeed( b2JointId jointId, float motorSpeed );
float b2DistanceJoint_GetMotorSpeed( b2JointId jointId );
void b2DistanceJoint_SetMaxMotorForce( b2JointId jointId, float force );
float b2DistanceJoint_GetMaxMotorForce( b2JointId jointId );
float b2DistanceJoint_GetMotorForce( b2JointId jointId );
```


---
### b2MotorJoint <a name="b2motorjoint"></a>
```c
void b2MotorJoint_SetLinearVelocity( b2JointId jointId, b2Vec2 velocity );
b2Vec2 b2MotorJoint_GetLinearVelocity( b2JointId jointId );
void b2MotorJoint_SetAngularVelocity( b2JointId jointId, float velocity );
float b2MotorJoint_GetAngularVelocity( b2JointId jointId );
void b2MotorJoint_SetMaxVelocityForce( b2JointId jointId, float maxForce );
float b2MotorJoint_GetMaxVelocityForce( b2JointId jointId );
void b2MotorJoint_SetMaxVelocityTorque( b2JointId jointId, float maxTorque );
float b2MotorJoint_GetMaxVelocityTorque( b2JointId jointId );
void b2MotorJoint_SetLinearHertz( b2JointId jointId, float hertz );
float b2MotorJoint_GetLinearHertz( b2JointId jointId );
void b2MotorJoint_SetLinearDampingRatio( b2JointId jointId, float damping );
float b2MotorJoint_GetLinearDampingRatio( b2JointId jointId );
void b2MotorJoint_SetAngularHertz( b2JointId jointId, float hertz );
float b2MotorJoint_GetAngularHertz( b2JointId jointId );
void b2MotorJoint_SetAngularDampingRatio( b2JointId jointId, float damping );
float b2MotorJoint_GetAngularDampingRatio( b2JointId jointId );
void b2MotorJoint_SetMaxSpringForce( b2JointId jointId, float maxForce );
float b2MotorJoint_GetMaxSpringForce( b2JointId jointId );
void b2MotorJoint_SetMaxSpringTorque( b2JointId jointId, float maxTorque );
float b2MotorJoint_GetMaxSpringTorque( b2JointId jointId );
```


---
### b2PrismaticJoint <a name="b2prismaticjoint"></a>
```c
void b2PrismaticJoint_EnableSpring( b2JointId jointId, bool enableSpring );
bool b2PrismaticJoint_IsSpringEnabled( b2JointId jointId );
void b2PrismaticJoint_SetSpringHertz( b2JointId jointId, float hertz );
float b2PrismaticJoint_GetSpringHertz( b2JointId jointId );
void b2PrismaticJoint_SetSpringDampingRatio( b2JointId jointId, float dampingRatio );
float b2PrismaticJoint_GetSpringDampingRatio( b2JointId jointId );
void b2PrismaticJoint_SetTargetTranslation( b2JointId jointId, float translation );
float b2PrismaticJoint_GetTargetTranslation( b2JointId jointId );
void b2PrismaticJoint_EnableLimit( b2JointId jointId, bool enableLimit );
bool b2PrismaticJoint_IsLimitEnabled( b2JointId jointId );
float b2PrismaticJoint_GetLowerLimit( b2JointId jointId );
float b2PrismaticJoint_GetUpperLimit( b2JointId jointId );
void b2PrismaticJoint_SetLimits( b2JointId jointId, float lower, float upper );
void b2PrismaticJoint_EnableMotor( b2JointId jointId, bool enableMotor );
bool b2PrismaticJoint_IsMotorEnabled( b2JointId jointId );
void b2PrismaticJoint_SetMotorSpeed( b2JointId jointId, float motorSpeed );
float b2PrismaticJoint_GetMotorSpeed( b2JointId jointId );
void b2PrismaticJoint_SetMaxMotorForce( b2JointId jointId, float force );
float b2PrismaticJoint_GetMaxMotorForce( b2JointId jointId );
float b2PrismaticJoint_GetMotorForce( b2JointId jointId );
float b2PrismaticJoint_GetTranslation( b2JointId jointId );
float b2PrismaticJoint_GetSpeed( b2JointId jointId );
```


---
### b2RevoluteJoint <a name="b2revolutejoint"></a>
```c
void b2RevoluteJoint_EnableSpring( b2JointId jointId, bool enableSpring );
bool b2RevoluteJoint_IsSpringEnabled( b2JointId jointId );
void b2RevoluteJoint_SetSpringHertz( b2JointId jointId, float hertz );
float b2RevoluteJoint_GetSpringHertz( b2JointId jointId );
void b2RevoluteJoint_SetSpringDampingRatio( b2JointId jointId, float dampingRatio );
float b2RevoluteJoint_GetSpringDampingRatio( b2JointId jointId );
void b2RevoluteJoint_SetTargetAngle( b2JointId jointId, float angle );
float b2RevoluteJoint_GetTargetAngle( b2JointId jointId );
float b2RevoluteJoint_GetAngle( b2JointId jointId );
void b2RevoluteJoint_EnableLimit( b2JointId jointId, bool enableLimit );
bool b2RevoluteJoint_IsLimitEnabled( b2JointId jointId );
float b2RevoluteJoint_GetLowerLimit( b2JointId jointId );
float b2RevoluteJoint_GetUpperLimit( b2JointId jointId );
void b2RevoluteJoint_SetLimits( b2JointId jointId, float lower, float upper );
void b2RevoluteJoint_EnableMotor( b2JointId jointId, bool enableMotor );
bool b2RevoluteJoint_IsMotorEnabled( b2JointId jointId );
void b2RevoluteJoint_SetMotorSpeed( b2JointId jointId, float motorSpeed );
float b2RevoluteJoint_GetMotorSpeed( b2JointId jointId );
float b2RevoluteJoint_GetMotorTorque( b2JointId jointId );
void b2RevoluteJoint_SetMaxMotorTorque( b2JointId jointId, float torque );
float b2RevoluteJoint_GetMaxMotorTorque( b2JointId jointId );
```


---
### b2WeldJoint <a name="b2weldjoint"></a>
```c
void b2WeldJoint_SetLinearHertz( b2JointId jointId, float hertz );
float b2WeldJoint_GetLinearHertz( b2JointId jointId );
void b2WeldJoint_SetLinearDampingRatio( b2JointId jointId, float dampingRatio );
float b2WeldJoint_GetLinearDampingRatio( b2JointId jointId );
void b2WeldJoint_SetAngularHertz( b2JointId jointId, float hertz );
float b2WeldJoint_GetAngularHertz( b2JointId jointId );
void b2WeldJoint_SetAngularDampingRatio( b2JointId jointId, float dampingRatio );
float b2WeldJoint_GetAngularDampingRatio( b2JointId jointId );
```


---
### b2WheelJoint <a name="b2wheeljoint"></a>
```c
void b2WheelJoint_EnableSpring( b2JointId jointId, bool enableSpring );
bool b2WheelJoint_IsSpringEnabled( b2JointId jointId );
void b2WheelJoint_SetSpringHertz( b2JointId jointId, float hertz );
float b2WheelJoint_GetSpringHertz( b2JointId jointId );
void b2WheelJoint_SetSpringDampingRatio( b2JointId jointId, float dampingRatio );
float b2WheelJoint_GetSpringDampingRatio( b2JointId jointId );
void b2WheelJoint_EnableLimit( b2JointId jointId, bool enableLimit );
bool b2WheelJoint_IsLimitEnabled( b2JointId jointId );
float b2WheelJoint_GetLowerLimit( b2JointId jointId );
float b2WheelJoint_GetUpperLimit( b2JointId jointId );
void b2WheelJoint_SetLimits( b2JointId jointId, float lower, float upper );
void b2WheelJoint_EnableMotor( b2JointId jointId, bool enableMotor );
bool b2WheelJoint_IsMotorEnabled( b2JointId jointId );
void b2WheelJoint_SetMotorSpeed( b2JointId jointId, float motorSpeed );
float b2WheelJoint_GetMotorSpeed( b2JointId jointId );
void b2WheelJoint_SetMaxMotorTorque( b2JointId jointId, float torque );
float b2WheelJoint_GetMaxMotorTorque( b2JointId jointId );
float b2WheelJoint_GetMotorTorque( b2JointId jointId );
```


---
### b2Contact <a name="b2contact"></a>

 * @defgroup contact Contact * Access to contacts * @{ 

```c
bool b2Contact_IsValid( b2ContactId id );
b2ContactData b2Contact_GetData( b2ContactId contactId );
```
