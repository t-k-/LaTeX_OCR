import tensorflow as tf

ctx_vector = []
class AttentionMechanism(object):
    """Class to compute attention over an image"""

    def __init__(self, img, dim_e, tiles=1):
        """Stores the image under the right shape.

        We loose the H, W dimensions and merge them into a single
        dimension that corresponds to "regions" of the image.

        Args:
            img: (tf.Tensor) image
            dim_e: (int) dimension of the intermediary vector used to compute attention
            tiles: (int) default 1, input to context h may have size (tile * batch_size, ...)

        """
        if len(img.shape) == 3:
            self._img = img
        elif len(img.shape) == 4:
            N    = tf.shape(img)[0]
            H, W = tf.shape(img)[1], tf.shape(img)[2] # image
            C    = img.shape[3].value                 # channels
            self._img = tf.reshape(img, shape=[N, H*W, C])
        else:
            print("Image shape not supported")
            raise NotImplementedError

        # dimensions
        self._n_regions  = tf.shape(self._img)[1] # H*W
        self._n_channels = self._img.shape[2].value # 由 decoder 决定 即卷积核个数 512
        self._dim_e      = dim_e # 256
        self._tiles      = tiles # 1 if config.decoding == "greedy" else config.beam_size
        self._scope_name = "att_mechanism"

        # print(self._n_regions, self._n_channels, self._dim_e, self._tiles, img.shape)
        # Tensor("attn_cell/strided_slice_4:0", shape=(), dtype=int32) 512 256 1 (?, ?, ?, 512)
        # a.shape and img.shape (?, ?, 1) (?, ?, 512)
        # Tensor("attn_cell_1/strided_slice_3:0", shape=(), dtype=int32) 512 256 2 (?, ?, ?, 512)
        # a.shape and img.shape (?, ?, 1) (?, ?, 512)
        # attention vector over the image
        self._att_img = tf.layers.dense(inputs=self._img, units=self._dim_e, use_bias=False, name="att_img")


    def context(self, h):
        """Computes attention

        Args:
            h: (batch_size, num_units) hidden state

        Returns:
            c: (batch_size, channels) context vector

        """
        with tf.variable_scope(self._scope_name):
            if self._tiles > 1: # self._tiles == config.beam_size
                att_img = tf.expand_dims(self._att_img, axis=1)
                att_img = tf.tile(att_img, multiples=[1, self._tiles, 1, 1])
                att_img = tf.reshape(att_img, shape=[-1, self._n_regions, self._dim_e]) # (tiles*batch, H*W, 256)
                img = tf.expand_dims(self._img, axis=1) # 增加一维给 beam_search
                img = tf.tile(img, multiples=[1, self._tiles, 1, 1]) # 在加的这一维上复制 beam_size 个一摸一样的
                img = tf.reshape(img, shape=[-1, self._n_regions, self._n_channels]) # (tiles*batch, H*W, 512)
            else:
                att_img = self._att_img
                img     = self._img

            # print(img.name, img.shape)
            # print(att_img.name, att_img.shape)
            # computes attention over the hidden vector
            att_h = tf.layers.dense(inputs=h, units=self._dim_e, use_bias=False)
            # print(h.name, h.shape)
            # print(att_h.name, att_h.shape)

            # sums the two contributions
            att_h = tf.expand_dims(att_h, axis=1)
            att = tf.tanh(att_img + att_h)

            # print(att.name, att.shape)

            # computes scalar product with beta vector
            # works faster with a matmul than with a * and a tf.reduce_sum
            att_beta = tf.get_variable("att_beta", shape=[self._dim_e, 1], dtype=tf.float32)
            att_flat = tf.reshape(att, shape=[-1, self._dim_e]) # 扁平化

            # --- debug
            # print(att_beta.name, att_beta.shape)
            # print(att_flat.name, att_flat.shape)
            # --- debug

            e = tf.matmul(att_flat, att_beta)
            e = tf.reshape(e, shape=[-1, self._n_regions])

            # print(e.name, e.shape)

            # compute weights
            a = tf.nn.softmax(e)

            # print(a.name, a.shape)

            # --- for visualize
            # 下个断点，检查 attention 以可视化
            def _debug_bkpt(val):
                global ctx_vector # 用全局变量实现可视化

                # TODO 下面的 if-else 会一直扩充 ctx_vector 可能导致 OOM
                # TODO 训练时注意注释掉
                # if not ctx_vector:
                #     ctx_vector = [val]
                # else:
                #     ctx_vector += [val]
                return False

            debug_print_op = tf.py_func(_debug_bkpt, [a], [tf.bool]) # 自定义一个 op 输入是 [a] 输出类型是 [tf.bool]
            with tf.control_dependencies(debug_print_op):
                # 声明 op 的执行依赖
                # 即在执行下面这行代码前，必先执行  debug_print_op
                a = tf.identity(a, name='a_for_visualize')
            # --- for visualize

            # print(a.name, a.shape)

            a = tf.expand_dims(a, axis=-1)

            # print(a.name, a.shape)
            c = tf.reduce_sum(a * img, axis=1)

            # print(c.name, c.shape)

            # print("a.shape and img.shape", a.shape, img.shape)

            return c


    def initial_cell_state(self, cell):
        """Returns initial state of a cell computed from the image

        Assumes cell.state_type is an instance of named_tuple.
        Ex: LSTMStateTuple

        Args:
            cell: (instance of RNNCell) must define _state_size

        """
        _states_0 = []
        for hidden_name in cell._state_size._fields:
            hidden_dim = getattr(cell._state_size, hidden_name)
            h = self.initial_state(hidden_name, hidden_dim)
            _states_0.append(h)

        initial_state_cell = type(cell.state_size)(*_states_0)

        return initial_state_cell


    def initial_state(self, name, dim):
        """Returns initial state of dimension specified by dim"""
        with tf.variable_scope(self._scope_name):
            img_mean = tf.reduce_mean(self._img, axis=1)
            W = tf.get_variable("W_{}_0".format(name), shape=[self._n_channels, dim])
            b = tf.get_variable("b_{}_0".format(name), shape=[dim])
            h = tf.tanh(tf.matmul(img_mean, W) + b)

            return h