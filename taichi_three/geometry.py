import taichi as ti
import taichi_glsl as ts


@ti.data_oriented
class Geometry(ts.TaichiClass):
    @ti.func
    def render(self):
        for I in ti.grouped(ti.ndrange(*self.loop_range().shape())):
            self.subscript(I).do_render()

    def subscript(self, I):
        ret = self._subscript(I)
        try:
            ret.model = self.model
        except AttributeError:
            pass
        return ret

    def do_render(self):
        raise NotImplementedError


class Vertex(Geometry):
    @property
    def pos(self):
        return self.entries[0]

    @property
    def tex(self):
        return self.entries[1]

    @classmethod
    def _var(cls, shape=None, has_tex=False):
        ret = []
        ret.append(ti.Vector.var(3, ti.f32, shape))
        if has_tex:
            ret.append(ti.Vector.var(2, ti.f32, shape))
        return ret


class Line(Geometry):
    @property
    def idx(self):
        return self.entries[0]

    @classmethod
    def _var(cls, shape=None):
        return ti.Vector.var(2, ti.i32, shape)

    @ti.func
    def vertex(self, i: ti.template()):
        model = self.model
        return model.vertices[self.idx[i]]

    @ti.func
    def do_render(self):
        scene = self.model.scene
        W = 1
        A = scene.uncook_coor(scene.camera.untrans_pos(self.vertex(0).pos))
        B = scene.uncook_coor(scene.camera.untrans_pos(self.vertex(1).pos))
        M, N = int(ti.floor(min(A, B) - W)), int(ti.ceil(max(A, B) + W))
        for X in ti.grouped(ti.ndrange((M.x, N.x), (M.y, N.y))):
            P = B - A
            udf = (ts.cross(X, P) + ts.cross(B, A))**2 / P.norm_sqr()
            XoP = ts.dot(X, P)
            AoB = ts.dot(A, B)
            if XoP > B.norm_sqr() - AoB:
                udf = (B - X).norm_sqr()
            elif XoP < AoB - A.norm_sqr():
                    udf = (A - X).norm_sqr()
            if udf < 0:
                scene.img[X] = ts.vec3(1.0)
            elif udf < W**2:
                t = ts.smoothstep(udf, 0, W**2)
                ti.atomic_min(scene.img[X], ts.vec3(t))


class Face(Geometry):
    @property
    def idx(self):
        return self.entries[0]

    @classmethod
    def _var(cls, shape=None):
        return ti.Vector.var(3, ti.i32, shape)

    @ti.func
    def vertex(self, i: ti.template()):
        model = self.model
        return model.vertices[self.idx[i]]

    @ti.func
    def do_render(self):
        model = self.model
        scene = model.scene
        L2W = model.L2W
        va, vb, vc = self.vertex(0), self.vertex(1), self.vertex(2)
        a = scene.camera.untrans_pos(L2W @ va.pos)
        b = scene.camera.untrans_pos(L2W @ vb.pos)
        c = scene.camera.untrans_pos(L2W @ vc.pos)
        A = scene.uncook_coor(a)
        B = scene.uncook_coor(b)
        C = scene.uncook_coor(c)
        B_A = B - A
        C_B = C - B
        A_C = A - C
        ilB_A = 1 / ts.length(B_A)
        ilC_B = 1 / ts.length(C_B)
        ilA_C = 1 / ts.length(A_C)
        B_A *= ilB_A
        C_B *= ilC_B
        A_C *= ilA_C
        BxA = ts.cross(B, A) * ilB_A
        CxB = ts.cross(C, B) * ilC_B
        AxC = ts.cross(A, C) * ilA_C
        normal = ts.normalize(ts.cross(a - c, a - b))
        light_dir = scene.camera.untrans_dir(scene.light_dir[None])
        center_pos = (a + b + c) / 3

        color = scene.opt.render_func(center_pos, normal, ts.vec3(0.0), light_dir)
        color = scene.opt.pre_process(color)

        Ak = 1 / (ts.cross(A, C_B) + CxB)
        Bk = 1 / (ts.cross(B, A_C) + AxC)
        Ck = 1 / (ts.cross(C, B_A) + BxA)

        W = 1
        M = int(ti.floor(min(A, B, C) - W))
        N = int(ti.ceil(max(A, B, C) + W))
        for X in ti.grouped(ti.ndrange((M.x, N.x), (M.y, N.y))):
            AB = ts.cross(X, B_A) + BxA
            BC = ts.cross(X, C_B) + CxB
            CA = ts.cross(X, A_C) + AxC
            if AB <= 0 and BC <= 0 and CA <= 0:
                zindex = a.z * Ak * BC + b.z * Bk * CA + c.z * Ck * AB
                #zindex = center_pos.z

                if zindex >= ti.atomic_max(scene.zbuf[X], zindex):
                    texCoor = va.tex * Ak * BC + vb.tex * Bk * CA + vc.tex * Ck * AB

                    scene.img[X] = color * self.model.texSample(texCoor)
